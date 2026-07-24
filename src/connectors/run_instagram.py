from __future__ import annotations

import argparse
import os
import time
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
        "fields": "id,caption,media_type,like_count,comments_count,timestamp,media_url,permalink",
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
                "like_count": int(item.get("like_count", 0) or 0),
                "comments_count": int(item.get("comments_count", 0) or 0),
                "timestamp": item.get("timestamp"),
                "permalink": item.get("permalink"),
            }

        url = data.get("paging", {}).get("next")
        params = None


@dlt.resource(name="insights_daily", write_disposition="replace")
def get_insights(instagram_business_id: str, access_token: str):
    base_url = f"{INSTAGRAM_API_BASE}/{instagram_business_id}/insights"

    # Call 1: time_series metrics - per-day data
    time_metrics = ["reach", "follower_count"]
    ts_params = {
        "metric": ",".join(time_metrics),
        "period": "day",
        "metric_type": "time_series",
        "access_token": access_token,
    }
    ts_data = _do_request(base_url, ts_params, f"instagram {instagram_business_id} time_series")

    metric_values: dict[str, Any] = {}
    for insight in ts_data.get("data", []):
        metric_name = insight.get("name")
        for value in insight.get("values", []):
            date = (value.get("end_time") or "")[:10]
            if not date:
                continue
            metric_values.setdefault(date, {})[metric_name] = value.get("value")

    # Call 2: total_value metrics - single cumulative sum
    total_metrics = ["profile_views", "views"]
    tv_params = {
        "metric": ",".join(total_metrics),
        "period": "day",
        "metric_type": "total_value",
        "access_token": access_token,
    }
    tv_data = _do_request(base_url, tv_params, f"instagram {instagram_business_id} total_value")

    total_values = {}
    for insight in tv_data.get("data", []):
        name = insight.get("name")
        tv = insight.get("total_value", {})
        total_values[name] = tv.get("value")

    # Merge: yield a row for each time_series date + the latest total_values
    for date, vals in sorted(metric_values.items()):
        yield {
            "report_date": date,
            "reach": vals.get("reach"),
            "views": total_values.get("views"),
            "profile_views": total_values.get("profile_views"),
            "follower_count": vals.get("follower_count"),
        }


@dlt.source
def instagram_source(instagram_business_id: str, access_token: str):
    return [
        get_media(instagram_business_id, access_token),
        get_insights(instagram_business_id, access_token),
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
    info = pipeline.run(instagram_source(instagram_business_id, access_token))
    print(f"[INSTAGRAM] Done: {info}")
