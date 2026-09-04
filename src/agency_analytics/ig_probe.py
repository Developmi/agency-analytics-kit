"""Read-only Instagram insights probe (spec IG4, design D8).

A pure, side-effect-free measurement core plus a thin CLI. It answers, against
a real Instagram business account, the questions the backfill design needs:

  (a) which metrics respond today and with which ``metric_type``;
  (b) real total_value retention per trailing window (docs ceiling 90 days,
      follower_count 30 days) and whether the daily reach series responds past
      that ceiling;
  (c) which breakdown axes work per metric (docs matrix, verified live);
  (d) behavior below 100 followers and for empty datasets (never coerced to 0).

The probe NEVER writes to any database and makes only read-only Graph API GET
calls (account node + ``/insights`` windows). It is not part of the scheduled
pipeline, so it can never break it (IG4-R3).

Module layout mirrors ``agency_analytics/pipeline_plan.py``: a pure
``probe_core(...)`` that receives an injected ``fetch(url, params) -> dict``
(unit-tested with fake fetches) and a ``main()`` CLI that resolves the client
YAML/token exactly like ``src/connectors/run_instagram.py`` and wires a live
HTTP fetch.

Report schema (one JSON line; ``--output`` writes it to a file)::

    {"schema_version": "1.0", "status": "ok", "read_only": true,
     "generated_at": "<now ISO>",
     "account": {"id", "username", "followers_count", "eligibility": ">=100|<100"},
     "daily": {"reach": {"responds", "series_shape", "max_history_days", "evidence":
                 [{"offset_days", "window_end", "responds", "values"}]},
               "follower_count": {same shape}},
     "horizon": {"requested_days", "confirmed_days",
                 "evidence": [{"offset_days", "window_end", "responds",
                               "metrics": {metric: bool}}]},
     "metrics": {metric: {"responds", "metric_type", "per_window_value",
                          "state": "ok|empty|unsupported|gated_skipped",
                          "max_history_days"}},
     "breakdowns": {axis: {"supported": {metric: [dimension_values]},
                           "unsupported": [metric], "empty": [metric],
                           "skipped": [metric]}},
     "window_offsets_days": [...]}

Semantics:

* ``state == "ok"``      the API returned an entry with a real value (0 is a
                         real zero, never an empty dataset);
* ``state == "empty"``   the API answered ``data: []`` or omitted the metric —
                         stored as ``None``, never 0 (IG3-R2);
* ``state == "unsupported"``  an error-100 measurement (breakdown axis);
* ``state == "gated_skipped"``  not requested because followers < 100 (IG3-R1).

Breakdown candidates come from the official metric table (Instagram account
insights, docs updated 2026-06-16): ``media_product_type`` applies to
views/likes/comments/shares/saves/total_interactions, ``follow_type`` to
follows_and_unfollows, ``contact_button_type`` to profile_links_taps;
accounts_engaged/replies/reposts declare no breakdown and are never requested
with one (asking is an API error). Breakdowns are only valid with
``metric_type=total_value`` and are requested one metric per call.

Exit codes (all non-fatal for the pipeline — the probe is never wired in):

* 0  probe completed and printed/wrote the report;
* 3  probe could not run (missing --read-only, client file, token env, ...);
* 4  API/transport error aborted the run (clear status payload emitted).

Full-run call budget with defaults (horizon 90d, eligible account): 1 account
node + 5 reach windows + 2 follower_count windows + 4 total_value batch
windows + 2 gated singles + 8 breakdown calls = 22 read-only GETs. ``--horizon-days``,
``--reach-deep-offset-days`` and ``--skip-breakdowns`` shrink it.

Live invocation (run by the user against example, read-only)::

    python -m agency_analytics.ig_probe --client example --read-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

import requests
import yaml

# Keep in sync with src/connectors/run_instagram.py INSTAGRAM_API_BASE so the
# probe measures the exact API version the connector will hit.
API_BASE = "https://graph.facebook.com/v25.0"

# API limit: at most 30 days between since and until (same as the connector).
WINDOW_DAYS = 30
# Gating threshold (IG3-R1): gated metrics are only requested at >= 100.
FOLLOWERS_GATE = 100
# Docs ceiling for user total_value metrics; conservative probe default.
DEFAULT_HORIZON_DAYS = 90
# Extra reach-only probe past the total_value ceiling (daily series survive?).
DEFAULT_REACH_DEEP_OFFSET_DAYS = 200
# follower_count is documented for the last 30 days only.
FOLLOWER_COUNT_RETENTION_DAYS = 30

# Ungated total_value candidates (comma-joined in one call per window).
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
# Gated total_value candidates (IG3-R1): single dedicated calls, >= 100 only.
TOTAL_VALUE_GATED_METRICS: tuple[str, ...] = (
    "follows_and_unfollows",
    "profile_links_taps",
)

REACH_METRIC = "reach"
FOLLOWER_COUNT_METRIC = "follower_count"

# Breakdown axes and their probe candidates, transcribed from the official
# metric table. Metrics documented as breakdown "n/a" are never requested with
# one. The live probe confirms which pairs actually respond (error 100 on a
# breakdown call marks the pair unsupported without aborting).
BREAKDOWN_AXES: tuple[str, ...] = (
    "media_product_type",
    "follow_type",
    "contact_button_type",
)
BREAKDOWN_CANDIDATES: dict[str, tuple[str, ...]] = {
    "media_product_type": (
        "views",
        "likes",
        "comments",
        "shares",
        "saves",
        "total_interactions",
    ),
    "follow_type": ("follows_and_unfollows",),
    "contact_button_type": ("profile_links_taps",),
}

PROBE_HTTP_TIMEOUT = 30
PROBE_HTTP_RETRIES = 3
PROBE_BACKOFF_BASE = 2
PROBE_BACKOFF_MAX = 30

EXIT_OK = 0
EXIT_CANNOT_RUN = 3
EXIT_PROBE_ERROR = 4

Fetch = Callable[[str, dict[str, Any]], dict[str, Any]]


class ProbeError(Exception):
    """Base class for probe failures."""


class ProbeCannotRunError(ProbeError):
    """Configuration/environment problem: the probe cannot start."""


class ProbeAPIError(ProbeError):
    """The Graph API returned an error payload that must abort the run."""

    def __init__(self, code: int, message: str):
        super().__init__(f"API error {code}: {message}")
        self.code = code
        self.message = message


class ProbeTransportError(ProbeError):
    """HTTP/network failure that must abort the run."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fetch_json(
    fetch: Fetch,
    url: str,
    params: dict[str, Any],
    *,
    tolerate_100: bool,
) -> dict[str, Any]:
    """Run one probe call and classify API error payloads.

    ``tolerate_100`` marks calls where error 100 is a MEASUREMENT (an
    unsupported breakdown or a no-data window) instead of an abort; every other
    error code aborts everywhere (e.g. 190 token expired, rate-limit codes).
    """
    data = fetch(url, params)
    error = data.get("error") if isinstance(data, dict) else None
    if not error:
        return data
    code = int(error.get("code", 0))
    message = str(error.get("message", ""))
    if code == 100 and tolerate_100:
        return data
    raise ProbeAPIError(code, message)


def _window_offsets(horizon_days: int) -> tuple[int, ...]:
    """Trailing 30-day window ends from now back to ``horizon_days``."""
    offsets = list(range(0, horizon_days + 1, WINDOW_DAYS))
    if offsets[-1] != horizon_days:
        offsets.append(horizon_days)
    return tuple(offsets)


def _window_params(now: datetime, offset_days: int) -> tuple[int, int, str]:
    """Deterministic (since, until, window_end) for a window ending at the
    given offset: ``until = now - offset``, width = WINDOW_DAYS (design NFR-2)."""
    until_dt = now - timedelta(days=offset_days)
    since_dt = until_dt - timedelta(days=WINDOW_DAYS)
    return (
        int(since_dt.timestamp()),
        int(until_dt.timestamp()),
        until_dt.date().isoformat(),
    )


def _series_summary(results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    responding = [item["offset_days"] for item in results if item["responds"]]
    return {
        "responds": bool(responding),
        "max_history_days": max(responding) if responding else None,
        "evidence": list(results),
    }


def _probe_series_window(
    fetch: Fetch,
    insights_url: str,
    token: str,
    metric: str,
    since_ts: int,
    until_ts: int,
    offset_days: int,
    window_end: str,
) -> dict[str, Any]:
    """Probe one time_series window; error 100 counts as a non-responding
    window (a possible cliff signal), everything else aborts."""
    params = {
        "metric": metric,
        "period": "day",
        "metric_type": "time_series",
        "since": since_ts,
        "until": until_ts,
        "access_token": token,
    }
    data = _fetch_json(fetch, insights_url, params, tolerate_100=True)
    error = data.get("error") if isinstance(data, dict) else None
    if error is not None:
        return {
            "offset_days": offset_days,
            "window_end": window_end,
            "responds": False,
            "values": None,
            "error": f"code {error.get('code')}: {error.get('message')}",
        }
    value_count = 0
    for insight in data.get("data", []):
        value_count += len(insight.get("values", []))
    return {
        "offset_days": offset_days,
        "window_end": window_end,
        "responds": value_count > 0,
        "values": value_count,
    }


def _total_value(insight: dict[str, Any]) -> Any:
    return (insight.get("total_value") or {}).get("value")


def probe_core(
    business_id: str,
    token: str,
    fetch: Fetch,
    now: datetime,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    metrics: Sequence[str] | None = None,
    axes: Sequence[str] | None = None,
    skip_breakdowns: bool = False,
    reach_deep_offset_days: int = DEFAULT_REACH_DEEP_OFFSET_DAYS,
) -> dict[str, Any]:
    """Measure the account and return the IG4-R2 report (pure, no I/O).

    ``fetch(url, params) -> dict`` models the Graph API response; tests inject
    fake fetches, the CLI wires a live HTTP fetch. ``now`` is injected so the
    window math is deterministic in tests (NFR-2).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    common_metrics = tuple(metrics) if metrics is not None else TOTAL_VALUE_COMMON_METRICS
    requested_axes = tuple(axes) if axes is not None else BREAKDOWN_AXES

    account_url = f"{API_BASE}/{business_id}"
    insights_url = f"{API_BASE}/{business_id}/insights"
    account_params = {
        "fields": "id,username,followers_count",
        "access_token": token,
    }
    account = _fetch_json(fetch, account_url, account_params, tolerate_100=False)
    followers_count = account.get("followers_count")
    eligible = followers_count is not None and int(followers_count) >= FOLLOWERS_GATE

    offsets = _window_offsets(horizon_days)
    reach_offsets = sorted(
        set(offsets) | ({reach_deep_offset_days} if reach_deep_offset_days > 0 else set())
    )
    fc_offsets = (
        (0, min(FOLLOWER_COUNT_RETENTION_DAYS, horizon_days))
        if horizon_days >= FOLLOWER_COUNT_RETENTION_DAYS
        else (0,)
    )

    reach_results: list[dict[str, Any]] = []
    for offset in reach_offsets:
        since_ts, until_ts, window_end = _window_params(now, offset)
        reach_results.append(
            _probe_series_window(
                fetch, insights_url, token, REACH_METRIC, since_ts, until_ts, offset, window_end
            )
        )

    fc_results: list[dict[str, Any]] = []
    for offset in fc_offsets:
        since_ts, until_ts, window_end = _window_params(now, offset)
        fc_results.append(
            _probe_series_window(
                fetch,
                insights_url,
                token,
                FOLLOWER_COUNT_METRIC,
                since_ts,
                until_ts,
                offset,
                window_end,
            )
        )

    # total_value retention: one comma-joined batch per trailing window.
    batch_metric = ",".join(common_metrics)
    horizon_evidence: list[dict[str, Any]] = []
    present_by_offset: dict[int, dict[str, bool]] = {}
    offset0_entries: dict[str, Any] = {}
    for offset in offsets:
        since_ts, until_ts, window_end = _window_params(now, offset)
        params = {
            "metric": batch_metric,
            "period": "day",
            "metric_type": "total_value",
            "since": since_ts,
            "until": until_ts,
            "access_token": token,
        }
        # Error 100 on the common/gated batch aborts (design D4 parity: it
        # would corrupt a window row), so tolerate_100=False here.
        data = _fetch_json(fetch, insights_url, params, tolerate_100=False)
        entries = {item.get("name"): item for item in data.get("data", [])}
        present = {name: name in entries for name in common_metrics}
        present_by_offset[offset] = present
        if offset == 0:
            offset0_entries = entries
        horizon_evidence.append(
            {
                "offset_days": offset,
                "window_end": window_end,
                "responds": any(present.values()),
                "metrics": present,
            }
        )

    confirmed_days = max((o for o in offsets if any(present_by_offset[o].values())), default=0)
    horizon = {
        "requested_days": horizon_days,
        "confirmed_days": confirmed_days,
        "evidence": horizon_evidence,
    }

    # Gated totals: single dedicated calls, >= 100 followers only (IG3-R1).
    gated_states: dict[str, dict[str, Any]] = {}
    if eligible:
        for name in TOTAL_VALUE_GATED_METRICS:
            since_ts, until_ts, _window_end = _window_params(now, 0)
            params = {
                "metric": name,
                "period": "day",
                "metric_type": "total_value",
                "since": since_ts,
                "until": until_ts,
                "access_token": token,
            }
            data = _fetch_json(fetch, insights_url, params, tolerate_100=False)
            entry = next((item for item in data.get("data", []) if item.get("name") == name), None)
            value = _total_value(entry) if entry is not None else None
            if value is None:
                gated_states[name] = {"state": "empty", "value": None}
            else:
                gated_states[name] = {"state": "ok", "value": value}
    else:
        for name in TOTAL_VALUE_GATED_METRICS:
            gated_states[name] = {"state": "gated_skipped", "value": None}

    # Per-metric report rows (common batch from the current window).
    metrics_report: dict[str, dict[str, Any]] = {}
    for name in common_metrics:
        entry = offset0_entries.get(name)
        value = _total_value(entry) if entry is not None else None
        # An entry whose total_value is null carries no number: absence is
        # recorded as NULL, never as a fabricated 0 (IG3-R2).
        state = "ok" if value is not None else "empty"
        present_offsets = [o for o in offsets if present_by_offset[o].get(name, False)]
        metrics_report[name] = {
            "responds": state == "ok",
            "metric_type": "total_value",
            "per_window_value": value,
            "state": state,
            "max_history_days": max(present_offsets) if present_offsets else None,
        }
    for name, status in gated_states.items():
        state = status["state"]
        metrics_report[name] = {
            "responds": state == "ok",
            "metric_type": "total_value",
            "per_window_value": status["value"] if state == "ok" else None,
            "state": state,
            "max_history_days": None if state != "ok" else 0,
        }

    # Breakdown matrix: one single-metric total_value call per candidate pair,
    # current window; error 100 marks the pair unsupported (discovery, D4).
    breakdowns: dict[str, dict[str, Any]] = {}
    if not skip_breakdowns:
        for axis in requested_axes:
            if axis not in BREAKDOWN_CANDIDATES:
                continue
            supported: dict[str, list[str]] = {}
            unsupported: list[str] = []
            empty: list[str] = []
            skipped: list[str] = []
            for metric in BREAKDOWN_CANDIDATES[axis]:
                if metric in TOTAL_VALUE_GATED_METRICS and not eligible:
                    skipped.append(metric)
                    continue
                since_ts, until_ts, _window_end = _window_params(now, 0)
                params = {
                    "metric": metric,
                    "period": "day",
                    "metric_type": "total_value",
                    "breakdown": axis,
                    "since": since_ts,
                    "until": until_ts,
                    "access_token": token,
                }
                data = _fetch_json(fetch, insights_url, params, tolerate_100=True)
                error = data.get("error") if isinstance(data, dict) else None
                if error is not None:
                    unsupported.append(metric)
                    continue
                entry = next(
                    (item for item in data.get("data", []) if item.get("name") == metric), None
                )
                if entry is None:
                    empty.append(metric)
                    continue
                dims: set[str] = set()
                tv = entry.get("total_value") or {}
                for breakdown in tv.get("breakdowns") or []:
                    for result in breakdown.get("results") or []:
                        dim_values = result.get("dimension_values") or []
                        if dim_values:
                            dims.add(str(dim_values[0]))
                supported[metric] = sorted(dims)
            breakdowns[axis] = {
                "supported": supported,
                "unsupported": unsupported,
                "empty": empty,
                "skipped": skipped,
            }

    account_username = account.get("username")
    return {
        "schema_version": "1.0",
        "status": "ok",
        "read_only": True,
        "generated_at": now.isoformat(),
        "account": {
            "id": business_id,
            "username": account_username,
            "followers_count": followers_count,
            "eligibility": ">=100" if eligible else "<100",
        },
        "daily": {
            "reach": {"series_shape": "time_series", **_series_summary(reach_results)},
            "follower_count": {"series_shape": "time_series", **_series_summary(fc_results)},
        },
        "horizon": horizon,
        "metrics": metrics_report,
        **({"breakdowns": breakdowns} if not skip_breakdowns else {}),
        "window_offsets_days": list(offsets),
    }


# ─── Live HTTP fetch (wired by main only; tests never hit it) ───────────────


def _live_fetch(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET one read-only endpoint with bounded retries on transient failures.

    Non-200 responses whose body carries a typed Graph error payload are
    returned as-is so ``probe_core`` can classify them (code 100 vs 190 etc.).
    """
    last_error: ProbeTransportError | None = None
    for attempt in range(PROBE_HTTP_RETRIES):
        try:
            response = requests.get(url, params=params, timeout=PROBE_HTTP_TIMEOUT)
        except requests.RequestException as exc:
            last_error = ProbeTransportError(0, f"network error: {exc}")
            if attempt < PROBE_HTTP_RETRIES - 1:
                time.sleep(min(PROBE_BACKOFF_BASE * (2**attempt), PROBE_BACKOFF_MAX))
                continue
            raise last_error

        if response.status_code == 200:
            try:
                data = response.json()
            except ValueError as exc:
                raise ProbeTransportError(200, f"invalid JSON body: {exc}") from exc
            if not isinstance(data, dict):
                raise ProbeTransportError(200, "unexpected non-object JSON body")
            return data

        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            return body  # typed payload; probe_core classifies it
        if response.status_code in (429, 500, 502, 503, 504) and attempt < PROBE_HTTP_RETRIES - 1:
            time.sleep(min(PROBE_BACKOFF_BASE * (2**attempt), PROBE_BACKOFF_MAX))
            continue
        raise ProbeTransportError(response.status_code, f"HTTP {response.status_code}")

    if last_error is not None:
        raise last_error
    raise ProbeTransportError(0, "unreachable: probe fetch loop exhausted")  # pragma: no cover


# ─── CLI (client YAML resolution mirrors run_instagram.py main) ─────────────


def _resolve_clients_dir(override: str | None) -> str:
    if override:
        return override
    env_dir = os.environ.get("CLIENTS_DIR")
    if env_dir:
        return env_dir
    container_dir = "/app/clients"
    if os.path.exists(container_dir):
        return container_dir
    return os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients")
    )


def _load_client(client: str, clients_dir: str) -> dict[str, str]:
    """Resolve business id + token env name from the client YAML."""
    client_file = os.path.join(clients_dir, f"{client}.yml")
    if not os.path.exists(client_file):
        raise ProbeCannotRunError(f"client file not found: {client_file}")
    with open(client_file) as fh:
        payload = yaml.safe_load(fh) or {}
    if not payload.get("active", True):
        raise ProbeCannotRunError(f"client {client} is not active")
    connector = (payload.get("connectors") or {}).get("instagram") or {}
    if not connector.get("enabled"):
        raise ProbeCannotRunError(f"instagram connector not enabled for client {client}")
    business_id = connector.get("instagram_business_id")
    token_env = connector.get("token_env")
    if not business_id:
        raise ProbeCannotRunError(f"instagram_business_id missing for client {client}")
    if not token_env:
        raise ProbeCannotRunError(f"instagram token_env missing for client {client}")
    return {"business_id": str(business_id), "token_env": str(token_env)}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agency_analytics.ig_probe",
        description=(
            "Read-only Instagram insights probe (IG4): metric/retention/"
            "breakdown matrix as one JSON line. Never writes to any database."
        ),
    )
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    parser.add_argument("--clients-dir", default=None, help="Override clients/ directory")
    parser.add_argument(
        "--business-id", default=None, help="Override the Instagram business id from the YAML"
    )
    parser.add_argument(
        "--token-env", default=None, help="Override the token env var name from the YAML"
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=DEFAULT_HORIZON_DAYS,
        help="total_value windows to probe back (default %(default)s; caps call count)",
    )
    parser.add_argument(
        "--reach-deep-offset-days",
        type=int,
        default=DEFAULT_REACH_DEEP_OFFSET_DAYS,
        help="extra reach-only window past the total_value ceiling (0 disables)",
    )
    parser.add_argument(
        "--skip-breakdowns",
        action="store_true",
        help="do not issue per-axis breakdown calls (budget cut)",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="REQUIRED acknowledgement: this probe only issues read-only GETs",
    )
    parser.add_argument("--output", default=None, help="Write the JSON report to this file")
    return parser.parse_args(argv)


def _emit(report: dict[str, Any], output: str | None) -> None:
    line = json.dumps(report)
    if output:
        with open(output, "w") as fh:
            fh.write(line + "\n")
    else:
        print(line)


def _error_report(reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "status": "error",
        "read_only": True,
        "generated_at": _now_utc().isoformat(),
        "error": {"kind": extra.pop("kind", "config"), "reason": reason, **extra},
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: resolve the client, run the probe, print one JSON line."""
    args = _parse_args(argv)
    if not args.read_only:
        _emit(_error_report("read-only acknowledgement required (--read-only)"), args.output)
        return EXIT_CANNOT_RUN

    try:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:
            pass

        clients_dir = _resolve_clients_dir(args.clients_dir)
        resolved = _load_client(args.client, clients_dir)
        business_id = args.business_id or resolved["business_id"]
        token_env = args.token_env or resolved["token_env"]
        token = os.environ.get(token_env)
        if not token:
            raise ProbeCannotRunError(f"token env var {token_env} is not set")
        report = probe_core(
            business_id,
            token,
            _live_fetch,
            _now_utc(),
            horizon_days=args.horizon_days,
            skip_breakdowns=args.skip_breakdowns,
            reach_deep_offset_days=args.reach_deep_offset_days,
        )
    except ProbeCannotRunError as exc:
        _emit(_error_report(str(exc)), args.output)
        return EXIT_CANNOT_RUN
    except ProbeAPIError as exc:
        _emit(
            _error_report(exc.message, kind="api", code=exc.code),
            args.output,
        )
        return EXIT_PROBE_ERROR
    except ProbeTransportError as exc:
        _emit(
            _error_report(str(exc), kind="transport", code=exc.status),
            args.output,
        )
        return EXIT_PROBE_ERROR

    _emit(report, args.output)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
