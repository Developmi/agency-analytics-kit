from __future__ import annotations

import argparse
import os
import time
from typing import Any

import dlt
import yaml
from dlt.sources.helpers import requests

FACEBOOK_API_BASE = "https://graph.facebook.com/v25.0"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120
RATE_LIMIT_CODES = {4, 17, 80000}


def _should_retry(data):
    if "error" not in data:
        return False, None
    error = data["error"]
    code = error.get("code", 0)
    msg = error.get("message", "")
    if code in RATE_LIMIT_CODES:
        wait = (
            error.get("error_user_title", 30) if code == 80000 else error.get("error_subcode", 30)
        )
        if isinstance(wait, str):
            wait = 60
        return True, wait
    elif code == 190:
        raise Exception(
            "[FACEBOOK] Invalid or expired token. "
            "Facebook Pages requires a Page Access Token (not the Ads token). "
            "Generate one via /me/accounts with pages_read_engagement permission."
        )
    else:
        raise Exception(f"[FACEBOOK] API error {code}: {msg}")


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
            print(f"[FACEBOOK] HTTP {status}: {error_msg}")
            # Rate limit - retry
            if status == 429 or error_code in (4, 17, 80000):
                if attempt < retries:
                    wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                    print(f"[FACEBOOK] Rate limited ({context}). Retrying in {wait}s...")
                    time.sleep(wait)
                    continue
                raise Exception(
                    f"[FACEBOOK] Rate limit exceeded after {retries} retries ({context})."
                )
            # Code 100 = Invalid parameter - abort immediately (no retry)
            if error_code == 100:
                raise Exception(f"[FACEBOOK] Invalid parameter: {error_msg}")
            # Token expired - abort
            if error_code == 190:
                raise Exception("[FACEBOOK] Token expired. Renew in .env and redeploy.")
            # Hard error - abort immediately
            raise Exception(f"[FACEBOOK] API error: {error_msg}")
        except requests.RequestException as e:
            print(f"[FACEBOOK] Request error ({context}): {e}")
            if attempt < retries:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(f"[FACEBOOK] Retrying in {wait}s (attempt {attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
            raise

        retry, wait_hint = _should_retry(data)
        if retry:
            if attempt < retries:
                wait = min(wait_hint or BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(f"[FACEBOOK] Rate limited ({context}). Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise Exception(f"[FACEBOOK] Rate limit exceeded after {retries} retries ({context}).")
        return data

    raise Exception(f"[FACEBOOK] Max retries exceeded ({context}).")


@dlt.resource(name="page_posts", write_disposition="replace")
def get_page_posts(page_id: str, access_token: str):
    url = f"{FACEBOOK_API_BASE}/{page_id}/posts"
    params: dict[str, Any] | None = {
        "fields": (
            "id,message,created_time,permalink_url,story,"
            "likes.summary(true),comments.summary(true),shares"
        ),
        "access_token": access_token,
        "limit": 100,
    }
    while url:
        data = _do_request(url, params, f"page {page_id} posts")

        for post in data.get("data", []):
            yield {
                "post_id": post.get("id"),
                "message": post.get("message"),
                "created_time": post.get("created_time"),
                "permalink_url": post.get("permalink_url"),
                "story": post.get("story"),
                "likes_count": post.get("likes", {}).get("summary", {}).get("total_count", 0) or 0,
                "comments_count": post.get("comments", {}).get("summary", {}).get("total_count", 0)
                or 0,
                "shares_count": (post.get("shares") or {}).get("count", 0) or 0,
            }

        url = data.get("paging", {}).get("next")
        params = None


@dlt.resource(name="feed", write_disposition="replace")
def get_page_feed(page_id: str, access_token: str):
    url = f"{FACEBOOK_API_BASE}/{page_id}/feed"
    params: dict[str, Any] | None = {
        "fields": (
            "id,message,created_time,permalink_url,story,from,"
            "likes.summary(true),comments.summary(true),shares"
        ),
        "access_token": access_token,
        "limit": 100,
    }
    while url:
        data = _do_request(url, params, f"page {page_id} feed")

        for item in data.get("data", []):
            author = item.get("from") or {}
            yield {
                "feed_item_id": item.get("id"),
                "message": item.get("message"),
                "created_time": item.get("created_time"),
                "permalink_url": item.get("permalink_url"),
                "story": item.get("story"),
                "author_id": author.get("id"),
                "author_name": author.get("name"),
                "likes_count": item.get("likes", {}).get("summary", {}).get("total_count", 0) or 0,
                "comments_count": item.get("comments", {}).get("summary", {}).get("total_count", 0)
                or 0,
                "shares_count": (item.get("shares") or {}).get("count", 0) or 0,
            }

        url = data.get("paging", {}).get("next")
        params = None


@dlt.resource(name="page_insights_daily", write_disposition="replace")
def get_page_insights(page_id: str, access_token: str):
    metrics = [
        "page_total_media_view_unique",
        "page_media_view",
        "page_video_views",
        "page_views_total",
        "page_daily_follows",
        "page_total_actions",
    ]
    url = f"{FACEBOOK_API_BASE}/{page_id}/insights"
    params = {
        "metric": ",".join(metrics),
        "period": "day",
        "access_token": access_token,
    }

    data = _do_request(url, params, f"page {page_id} insights")

    metric_values: dict[str, Any] = {}
    for insight in data.get("data", []):
        metric_name = insight.get("name")
        for value in insight.get("values", []):
            date = (value.get("end_time") or "")[:10]
            if not date:
                continue
            metric_values.setdefault(date, {})[metric_name] = value.get("value")

    for date, vals in sorted(metric_values.items()):
        yield {
            "report_date": date,
            "page_total_media_view_unique": vals.get("page_total_media_view_unique"),
            "page_media_view": vals.get("page_media_view"),
            "page_video_views": vals.get("page_video_views"),
            "page_views_total": vals.get("page_views_total"),
            "page_daily_follows": vals.get("page_daily_follows"),
            "page_total_actions": vals.get("page_total_actions"),
        }


@dlt.source
def facebook_page_source(page_id: str, access_token: str):
    return [
        get_page_posts(page_id, access_token),
        get_page_feed(page_id, access_token),
        get_page_insights(page_id, access_token),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Facebook Page Insights dlt extractor")
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
        print(f"[FACEBOOK] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[FACEBOOK] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("facebook", {})
    if not connector.get("enabled"):
        print(f"[FACEBOOK] Facebook connector not enabled for client {args.client}. Skipping.")
        exit(0)

    page_id = connector["page_id"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[FACEBOOK] Extracting data for client '{args.client}' (page {page_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"facebook_{args.client}",
        destination="postgres",
        dataset_name="raw_facebook",
    )
    info = pipeline.run(facebook_page_source(page_id, access_token))
    print(f"[FACEBOOK] Done: {info}")
