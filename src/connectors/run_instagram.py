from __future__ import annotations

import argparse
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import dlt
import yaml
from dlt.sources.helpers import requests

INSTAGRAM_API_BASE = "https://graph.facebook.com/v25.0"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120
RATE_LIMIT_CODES = {4, 17, 80000}


class _InstagramAPIError(Exception):
    """Instagram Graph API error carrying the typed code (100, 190, ...).

    Messages keep the historical format so existing callers/tests that match on
    the text keep working; the ``code`` attribute lets the totals resource
    degrade a breakdown axis on error 100 (design D4) without catching every
    Exception.
    """

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _should_retry(data):
    if "error" not in data:
        return False
    error = data["error"]
    code = error.get("code", 0)
    msg = error.get("message", "")
    if code in RATE_LIMIT_CODES:
        wait = (
            error.get("error_user_title", 30) if code == 80000 else error.get("error_subcode", 30)
        )
        if isinstance(wait, str):
            wait = 60
        return wait
    if code == 100:
        raise _InstagramAPIError(f"[INSTAGRAM] Invalid parameter: {msg}", code=100)
    if code == 190:
        raise _InstagramAPIError(
            "[INSTAGRAM] Token expired. Renew the token in .env and redeploy.", code=190
        )
    raise _InstagramAPIError(f"[INSTAGRAM] API error {code}: {msg}", code=code)


def _do_request(url, params, context, retries=MAX_RETRIES):
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params)
            data = response.json()
        except requests.HTTPError as e:
            status = e.response.status_code
            try:
                error_data = e.response.json()
                error_code = error_data.get("error", {}).get("code", 0)
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_code = 0
                error_msg = str(e)
            print(f"[INSTAGRAM] HTTP {status}: {error_msg}")
            # Rate limit - retry
            if status == 429 or error_code in (4, 17, 80000):
                if attempt < retries:
                    wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                    print(f"[INSTAGRAM] Rate limited ({context}). Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise _InstagramAPIError(
                    f"[INSTAGRAM] Rate limit exceeded after {retries} retries ({context}).",
                    code=error_code,
                )
            # Code 100 = Invalid parameter - abort immediately (no retry)
            if error_code == 100:
                raise _InstagramAPIError(f"[INSTAGRAM] Invalid parameter: {error_msg}", code=100)
            # Token expired - abort
            if error_code == 190:
                raise _InstagramAPIError(
                    "[INSTAGRAM] Token expired. Renew in .env and redeploy.", code=190
                )
            # Hard error - abort immediately
            raise _InstagramAPIError(f"[INSTAGRAM] API error: {error_msg}", code=error_code)
        except requests.RequestException as e:
            print(f"[INSTAGRAM] Request error ({context}): {e}")
            if attempt < retries:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(f"[INSTAGRAM] Retrying in {wait}s (attempt {attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
            raise

        wait = _should_retry(data)
        if wait:
            if attempt < retries:
                w = min(wait, BACKOFF_MAX)
                print(f"[INSTAGRAM] Rate limited ({context}). Retrying in {w}s...")
                time.sleep(w)
                continue
            raise Exception(f"[INSTAGRAM] Rate limit exceeded after {retries} retries ({context}).")
        return data

    raise Exception(f"[INSTAGRAM] Max retries exceeded ({context}).")


@dlt.resource(name="media", write_disposition="replace")
def get_media(instagram_business_id: str, access_token: str):
    url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}/media"
    params: dict[str, Any] | None = {
        "fields": (
            "id,caption,media_type,like_count,comments_count,timestamp,"
            "media_url,permalink,thumbnail_url,shortcode,"
            "media_product_type,owner{id},is_comment_enabled"
        ),
        "access_token": access_token,
        "limit": 100,
    }
    while url:
        data = _do_request(url, params, f"instagram {instagram_business_id} media")
        for item in data.get("data", []):
            yield {
                "media_id": item.get("id"),
                "caption": item.get("caption"),
                "media_type": item.get("media_type"),
                "media_url": item.get("media_url"),
                "permalink": item.get("permalink"),
                "thumbnail_url": item.get("thumbnail_url"),
                "shortcode": item.get("shortcode"),
                "media_product_type": item.get("media_product_type"),
                "owner_id": (item.get("owner") or {}).get("id"),
                "is_comment_enabled": item.get("is_comment_enabled"),
                "like_count": int(item.get("like_count", 0) or 0),
                "comments_count": int(item.get("comments_count", 0) or 0),
                "timestamp": item.get("timestamp"),
            }

        url = data.get("paging", {}).get("next")
        params = None


MAX_INSTAGRAM_WINDOW_DAYS = 30  # API limit: 30 days between since and until
FC_WINDOW_DAYS = 30  # follower_count only available for last 30 days

# ─── Probe-transcribed constants (live probe obs #537, 2026-09-04) ───────────
# horizon: total_value responds at offsets 0/30/60/90 → confirmed 90d retention
# (docs ceiling for user metrics; design D2 default). No invented range.
TOTALS_HORIZON_DAYS = 90
# Gating threshold (IG3-R1): gated metrics only requested at >= 100 followers.
FOLLOWERS_GATE = 100

# Ungated total_value metrics, comma-joined into ONE call per window (IG2-R1;
# probe horizon table: views/likes/comments/shares/saves/total_interactions/
# accounts_engaged/replies/reposts all respond at every window).
TOTAL_VALUE_COMMON_METRICS: tuple[str, ...] = (
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "total_interactions",
    "accounts_engaged",
    "replies",
    "reposts",
)
# Gated total_value metrics (IG3-R1). Live probe: profile_links_taps answers
# value 0 ONLY in the current window (max_history 0) and follows_and_unfollows
# is empty even when eligible — both are optional: an empty/absent dataset
# stores NULL and never aborts (obs #537 #4/#5/#7).
TOTAL_VALUE_GATED_METRICS: tuple[str, ...] = (
    "follows_and_unfollows",
    "profile_links_taps",
)

# Confirmed breakdown axes per metric, transcribed from the live probe
# (obs #537 #6): media_product_type responds for exactly these six common
# metrics (AD/CAROUSEL_CONTAINER/POST/REEL/STORY per metric); follow_type lists
# FOLLOWER/NON_FOLLOWER for follows_and_unfollows even when the base metric is
# empty. accounts_engaged/replies/reposts declare no breakdown and are NEVER
# requested with one; contact_button_type came back empty (no buttons) so it is
# not requested either (design D4: empty intersect ⇒ no call).
BREAKDOWN_CONFIRMED_METRICS_BY_AXIS: dict[str, tuple[str, ...]] = {
    "media_product_type": (
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
        "total_interactions",
    ),
    "follow_type": ("follows_and_unfollows",),
}


def _tv_value(insight):
    """total_value.value of one insight entry (None when absent/null)."""
    return (insight.get("total_value") or {}).get("value")


def _parse_total_values(data, metrics):
    """Map metric name -> total_value.value (None when absent/null)."""
    by_name = {}
    for insight in data.get("data", []):
        by_name[insight.get("name")] = _tv_value(insight)
    return {name: by_name.get(name) for name in metrics}


def _fetch_reach_by_date(base_url, since_ts, until_ts, access_token, context):
    """Fetch one 30-day window of reach (time_series). Returns dict[date]->value.

    One call per window; no total_value metric is requested or parsed here —
    window-scoped scalars live in the totals resource (IG1-R3).
    """
    params_ts = {
        "metric": "reach",
        "period": "day",
        "metric_type": "time_series",
        "since": since_ts,
        "until": until_ts,
        "access_token": access_token,
    }
    ts_data = _do_request(base_url, params_ts, f"{context} ts s={since_ts}")

    reach_by_date: dict[str, Any] = {}
    for insight in ts_data.get("data", []):
        for value in insight.get("values", []):
            date = (value.get("end_time") or "")[:10]
            if date:
                reach_by_date[date] = value.get("value")
    return reach_by_date


def _fetch_account_followers_count(base_url, access_token, context):
    """Account node followers_count (one call, node fields) for the >=100 gate.

    IG3-R1/D5: the gate value comes from the API response itself — no extra
    gating call and no dependency on the business_profile resource.
    """
    params = {"fields": "followers_count", "access_token": access_token}
    data = _do_request(base_url, params, f"{context} followers_count")
    return data.get("followers_count")


def _totals_windows(now, horizon_days):
    """Split ``[now - horizon_days, now]`` into consecutive <=30d windows.

    Deterministic per run anchor (NFR-2/D2): each window carries the API
    ``since_ts``/``until_ts`` plus the row identity ``date_start`` (inclusive
    first day) and ``date_end`` (inclusive last day = ``until_ts`` minus one
    day, so the partial current day is never included). Returns newest-first
    windows (offset 0, 30, ...) — empty when ``horizon_days <= 0``.
    """
    if horizon_days <= 0:
        return []
    windows: list[dict[str, Any]] = []
    until_dt = now
    earliest_dt = now - timedelta(days=horizon_days)
    while until_dt > earliest_dt:
        offset_days = (now - until_dt).days
        since_dt = until_dt - timedelta(days=MAX_INSTAGRAM_WINDOW_DAYS)
        if since_dt < earliest_dt:
            since_dt = earliest_dt
        windows.append(
            {
                "offset_days": offset_days,
                "since_ts": int(since_dt.timestamp()),
                "until_ts": int(until_dt.timestamp()),
                "date_start": since_dt.date().isoformat(),
                "date_end": (until_dt - timedelta(days=1)).date().isoformat(),
            }
        )
        until_dt = since_dt
    return windows


def _fetch_follower_count(base_url, access_token, context):
    """Fetch follower_count for the last 30 days (time_series). Returns dict[date] -> count."""
    now = datetime.now(timezone.utc)
    params = {
        "metric": "follower_count",
        "period": "day",
        "metric_type": "time_series",
        "since": int((now - timedelta(days=FC_WINDOW_DAYS)).timestamp()),
        "until": int(now.timestamp()),
        "access_token": access_token,
    }
    data = _do_request(base_url, params, f"{context} follower_count")

    values: dict[str, Any] = {}
    for insight in data.get("data", []):
        for value in insight.get("values", []):
            date = (value.get("end_time") or "")[:10]
            if date:
                values[date] = value.get("value")
    return values


def _fetch_common_total_values(insights_url, access_token, window, context):
    """One comma-joined total_value call per window (NFR-4).

    Returns per-metric values (None when the metric is absent/null). Whether the
    whole window carries no data is decided by the caller (IG3-R2: absence is
    never coerced to 0).
    """
    params = {
        "metric": ",".join(TOTAL_VALUE_COMMON_METRICS),
        "period": "day",
        "metric_type": "total_value",
        "since": window["since_ts"],
        "until": window["until_ts"],
        "access_token": access_token,
    }
    data = _do_request(
        insights_url,
        params,
        f"{context} totals s={window['since_ts']}",
    )
    return _parse_total_values(data, TOTAL_VALUE_COMMON_METRICS)


def _fetch_gated_total(insights_url, access_token, window, metric, context):
    """One single-metric total_value call for a gated metric (IG3-R1).

    Only invoked for the current window of an eligible account (probe: the gated
    endpoints answer, possibly empty, in the current window only). An empty
    ``data: []`` returns None — never 0 (IG3-R2); API errors abort (design D4
    parity: an error where the endpoint is expected to answer is a real fault).
    """
    params = {
        "metric": metric,
        "period": "day",
        "metric_type": "total_value",
        "since": window["since_ts"],
        "until": window["until_ts"],
        "access_token": access_token,
    }
    data = _do_request(
        insights_url,
        params,
        f"{context} {metric} s={window['since_ts']}",
    )
    entry = next((item for item in data.get("data", []) if item.get("name") == metric), None)
    return _tv_value(entry) if entry is not None else None


def _breakdown_applies(axis_metrics, window, eligible):
    """Whether a breakdown axis is requested for a window.

    Axes over gated metrics (follow_type -> follows_and_unfollows) exist only in
    the current window of an eligible account (probe: gated endpoints answer
    only there); axes over common metrics apply to every window.
    """
    has_gated = any(metric in TOTAL_VALUE_GATED_METRICS for metric in axis_metrics)
    if has_gated:
        return eligible and window["offset_days"] == 0
    return True


def _fetch_breakdown(insights_url, access_token, window, axis, axis_metrics, context):
    """One dedicated per-axis total_value call with ``breakdown=<axis>``.

    Breakdowns are never combined with ``time_series`` and only the probe-
    confirmed metrics are requested with an axis (IG2-R2). Error 100 here means
    the axis drifted unsupported -> the caller disables it for the run; other
    API errors abort.
    """
    params = {
        "metric": ",".join(axis_metrics),
        "period": "day",
        "metric_type": "total_value",
        "breakdown": axis,
        "since": window["since_ts"],
        "until": window["until_ts"],
        "access_token": access_token,
    }
    data = _do_request(
        insights_url,
        params,
        f"{context} breakdown {axis} s={window['since_ts']}",
    )
    rows: list[dict[str, Any]] = []
    for insight in data.get("data", []):
        name = insight.get("name")
        tv = insight.get("total_value") or {}
        for group in tv.get("breakdowns") or []:
            for result in group.get("results") or []:
                dim_values = result.get("dimension_values") or []
                if not dim_values:
                    continue
                rows.append(
                    {
                        "metric": name,
                        "breakdown": axis,
                        "dimension_value": str(dim_values[0]),
                        "value": result.get("value"),
                    }
                )
    return rows


@dlt.resource(name="insights_daily", write_disposition="replace")
def get_insights(instagram_business_id: str, access_token: str, insights_days_back: int = 729):
    """Per-day metrics only (IG1): reach over the full backfill window loop plus
    follower_count in the recent 30-day window where the API answers.

    Window-scoped ``total_value`` metrics moved to ``get_insights_totals``:
    this resource yields exactly ``{report_date, reach, follower_count}``, so no
    scalar can fan out over dates (IG1-R2) and no total_value column exists
    (IG1-R3). ``reach`` merges per date from its own window — no cross-window
    overwrite fabricates data (Scenario 1.2).
    """
    insights_url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}/insights"
    context = f"instagram {instagram_business_id}"

    now = datetime.now(timezone.utc)
    until_dt = now
    since_dt = now - timedelta(days=insights_days_back)

    reach_by_date: dict[str, Any] = {}
    window_start = since_dt
    while window_start < until_dt:
        window_end = min(window_start + timedelta(days=MAX_INSTAGRAM_WINDOW_DAYS), until_dt)
        window_reach = _fetch_reach_by_date(
            insights_url,
            int(window_start.timestamp()),
            int(window_end.timestamp()),
            access_token,
            context,
        )
        reach_by_date.update(window_reach)
        window_start = window_end

    # follower_count: only the last 30 days respond (probe obs #537 #2).
    follower_by_date = _fetch_follower_count(insights_url, access_token, context)

    for date in sorted(set(reach_by_date) | set(follower_by_date)):
        yield {
            "report_date": date,
            "reach": reach_by_date.get(date),
            "follower_count": follower_by_date.get(date),
        }


@dlt.resource(
    name="insights_totals",
    write_disposition="replace",
    columns={
        # dlt only materializes columns that receive data; gated metrics can be
        # 100% NULL on a healthy account (follows_and_unfollows is empty even
        # when eligible, probe obs #537 #5), so without explicit hints the raw
        # table would lack them and the totals staging model would fail. The
        # hints keep the schema stable: bigint columns, always present, NULL =
        # absence (IG3-R1/R2, WU5 gate finding).
        metric: {"data_type": "bigint", "nullable": True}
        for metric in TOTAL_VALUE_GATED_METRICS
    },
)
def get_insights_totals(
    instagram_business_id: str,
    access_token: str,
    horizon_days: int = TOTALS_HORIZON_DAYS,
):
    """Per-window total_value rows (IG2) + nested breakdowns child table.

    One deterministic row per trailing <=30d window over the probe horizon
    (default 90d, obs #537 #3) identified by ``date_start``/``date_end`` from a
    fixed run anchor (NFR-2). ``write_disposition=replace`` re-fetches the whole
    horizon every run and overwrites, never accumulating duplicates (NFR-1,
    Scenario 2.3). A window whose API response carries no data yields NO row
    (Scenario 2.4); gated metrics are fetched only >= 100 followers (IG3-R1).

    Nested ``breakdowns`` rows auto-normalize to the dlt child table
    ``insights_totals__breakdowns`` (design D3; existing child-table pattern).
    """
    base_url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}"
    insights_url = f"{base_url}/insights"
    context = f"instagram {instagram_business_id}"

    # Gate once per run from the account node the API itself reports (D5) —
    # no extra gating request.
    followers_count = _fetch_account_followers_count(base_url, access_token, context)
    eligible = followers_count is not None and int(followers_count) >= FOLLOWERS_GATE

    disabled_axes: set[str] = set()
    for window in _totals_windows(datetime.now(timezone.utc), horizon_days):
        common_values = _fetch_common_total_values(insights_url, access_token, window, context)
        if not any(value is not None for value in common_values.values()):
            # Whole window answered data: [] / no values -> absence: no row.
            continue

        row: dict[str, Any] = {
            "date_start": window["date_start"],
            "date_end": window["date_end"],
            **common_values,
            # Gated columns are always present: real value when the eligible
            # current window answers it, NULL otherwise (IG3-R1, Scenario 3.2).
            "follows_and_unfollows": None,
            "profile_links_taps": None,
        }
        if eligible and window["offset_days"] == 0:
            for metric in TOTAL_VALUE_GATED_METRICS:
                row[metric] = _fetch_gated_total(
                    insights_url, access_token, window, metric, context
                )

        breakdown_rows: list[dict[str, Any]] = []
        for axis, axis_metrics in BREAKDOWN_CONFIRMED_METRICS_BY_AXIS.items():
            if axis in disabled_axes or not _breakdown_applies(axis_metrics, window, eligible):
                continue
            try:
                breakdown_rows.extend(
                    _fetch_breakdown(
                        insights_url, access_token, window, axis, axis_metrics, context
                    )
                )
            except _InstagramAPIError as exc:
                if exc.code != 100:
                    raise
                print(
                    f"[INSTAGRAM] Breakdown {axis} unsupported for metrics "
                    f"{','.join(axis_metrics)}; disabling for this run."
                )
                disabled_axes.add(axis)
        row["breakdowns"] = breakdown_rows
        yield row


@dlt.resource(name="business_profile", write_disposition="replace")
def get_business_profile(instagram_business_id: str, access_token: str):
    state = dlt.current.resource_state()
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_run") == today:
        return

    url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}"
    params: dict[str, Any] = {
        "fields": (
            "id,username,name,profile_picture_url,"
            "biography,website,followers_count,follows_count,media_count"
        ),
        "access_token": access_token,
    }
    data = _do_request(url, params, f"instagram {instagram_business_id} profile")

    yield {
        "ig_id": data.get("id"),
        "username": data.get("username"),
        "name": data.get("name"),
        "profile_picture_url": data.get("profile_picture_url"),
        "biography": data.get("biography"),
        "website": data.get("website"),
        "followers_count": data.get("followers_count"),
        "follows_count": data.get("follows_count"),
        "media_count": data.get("media_count"),
    }

    state["last_run"] = today


@dlt.source
def instagram_source(
    instagram_business_id: str,
    access_token: str,
    insights_days_back: int = 729,
    totals_horizon_days: int = TOTALS_HORIZON_DAYS,
):
    # IG7-R1 additive-first: insights_totals precedes insights_daily in the
    # source list so the additive table is created before the daily shrink.
    return [
        get_media(instagram_business_id, access_token),
        get_insights_totals(instagram_business_id, access_token, totals_horizon_days),
        get_insights(instagram_business_id, access_token, insights_days_back),
        get_business_profile(instagram_business_id, access_token),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Instagram Business dlt extractor")
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    args = parser.parse_args()

    clients_dir = os.environ.get("CLIENTS_DIR")
    if not clients_dir:
        clients_dir = "/app/clients"
        if not os.path.exists(clients_dir):
            clients_dir = os.path.normpath(
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients")
            )
    client_file = f"{clients_dir}/{args.client}.yml"

    if not os.path.exists(client_file):
        print(f"[INSTAGRAM] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[INSTAGRAM] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("instagram", {})
    if not connector.get("enabled"):
        print(f"[INSTAGRAM] Instagram connector not enabled for client {args.client}. Skipping.")
        exit(0)

    instagram_business_id = connector["instagram_business_id"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(
        f"[INSTAGRAM] Extracting data for client '{args.client}'"
        f" (account {instagram_business_id})..."
    )

    pipeline = dlt.pipeline(
        pipeline_name=f"instagram_{args.client}",
        destination="postgres",
        dataset_name="raw_instagram",
    )
    insights_days_back = connector.get("insights_days_back", 729)

    info = pipeline.run(instagram_source(instagram_business_id, access_token, insights_days_back))
    print(f"[INSTAGRAM] Done: {info}")
