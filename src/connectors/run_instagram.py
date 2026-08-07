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
        raise Exception(f"[INSTAGRAM] Invalid parameter: {msg}")
    if code == 190:
        raise Exception("[INSTAGRAM] Token expired. Renew the token in .env and redeploy.")
    raise Exception(f"[INSTAGRAM] API error {code}: {msg}")


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
                raise Exception(
                    f"[INSTAGRAM] Rate limit exceeded after {retries} retries ({context})."
                )
            # Code 100 = Invalid parameter - abort immediately (no retry)
            if error_code == 100:
                raise Exception(f"[INSTAGRAM] Invalid parameter: {error_msg}")
            # Token expired - abort
            if error_code == 190:
                raise Exception("[INSTAGRAM] Token expired. Renew in .env and redeploy.")
            # Hard error - abort immediately
            raise Exception(f"[INSTAGRAM] API error: {error_msg}")
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


def _fetch_window_metrics(base_url, since_ts, until_ts, access_token, context):
    """Fetch one 30-day window of reach (time_series) + views/profile_views (total_value).
    Returns (reach_by_date, total_values_dict)."""
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

    params_tv = {
        "metric": (
            "views,profile_views,"
            "likes,comments,shares,saves,"
            "total_interactions,accounts_engaged,"
            "website_clicks"
        ),
        "period": "day",
        "metric_type": "total_value",
        "since": since_ts,
        "until": until_ts,
        "access_token": access_token,
    }
    tv_data = _do_request(base_url, params_tv, f"{context} tv s={since_ts}")

    total_values = {}
    for insight in tv_data.get("data", []):
        name = insight.get("name")
        tv = insight.get("total_value", {})
        total_values[name] = tv.get("value")

    return reach_by_date, total_values


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


@dlt.resource(name="insights_daily", write_disposition="replace")
def get_insights(instagram_business_id: str, access_token: str, insights_days_back: int = 729):
    base_url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}/insights"

    now = datetime.now(timezone.utc)
    until_dt = now
    since_dt = now - timedelta(days=insights_days_back)

    # Iterate 30-day windows — all metrics have this limit
    all_reach: dict[str, Any] = {}
    all_total: dict[str, Any] = {}

    window_start = since_dt
    while window_start < until_dt:
        window_end = min(window_start + timedelta(days=MAX_INSTAGRAM_WINDOW_DAYS), until_dt)
        s_ts = int(window_start.timestamp())
        e_ts = int(window_end.timestamp())

        reach_by_date, tv = _fetch_window_metrics(
            base_url,
            s_ts,
            e_ts,
            access_token,
            f"instagram {instagram_business_id}",
        )
        all_reach.update(reach_by_date)
        # Keep updating total_values so the latest window's values win
        all_total.update(tv)

        window_start = window_end

    # Follower count: only the last 30 days
    follower_by_date = _fetch_follower_count(
        base_url,
        access_token,
        f"instagram {instagram_business_id}",
    )

    all_dates = sorted(set(all_reach.keys()) | set(follower_by_date.keys()))
    for date in all_dates:
        yield {
            "report_date": date,
            "reach": all_reach.get(date),
            "views": all_total.get("views"),
            "profile_views": all_total.get("profile_views"),
            "follower_count": follower_by_date.get(date),
            # Total-value metrics
            "likes": all_total.get("likes"),
            "comments": all_total.get("comments"),
            "shares": all_total.get("shares"),
            "saves": all_total.get("saves"),
            "total_interactions": all_total.get("total_interactions"),
            "accounts_engaged": all_total.get("accounts_engaged"),
            "website_clicks": all_total.get("website_clicks"),
            "email_contacts": all_total.get("email_contacts"),
            "get_directions_clicks": all_total.get("get_directions_clicks"),
            "phone_call_clicks": all_total.get("phone_call_clicks"),
        }


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
def instagram_source(instagram_business_id: str, access_token: str, insights_days_back: int = 729):
    return [
        get_media(instagram_business_id, access_token),
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
