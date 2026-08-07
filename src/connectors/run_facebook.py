from __future__ import annotations

import argparse
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
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


def _execute_batch(batch_requests, access_token):
    url = FACEBOOK_API_BASE
    batch_json = json.dumps(batch_requests)
    params = {
        "batch": batch_json,
        "include_headers": "false",
        "access_token": access_token,
    }

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(url, params=params)
            results = response.json()
        except requests.HTTPError as e:
            status = e.response.status_code
            try:
                error_data = e.response.json()
                error_code = error_data.get("error", {}).get("code", 0)
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_code = 0
                error_msg = str(e)
            print(f"[FACEBOOK] Batch HTTP {status}: {error_msg}")
            if status == 429 or error_code in RATE_LIMIT_CODES:
                if attempt < MAX_RETRIES:
                    wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                    time.sleep(wait)
                    continue
                raise Exception(
                    f"[FACEBOOK] Batch rate limit exceeded after {MAX_RETRIES} retries."
                )
            if error_code == 190:
                raise Exception(
                    "[FACEBOOK] Invalid or expired token. "
                    "Facebook Pages requires a Page Access Token (not the Ads token). "
                    "Generate one via /me/accounts with pages_read_engagement permission."
                )
            raise Exception(f"[FACEBOOK] Batch HTTP error: {error_msg}")
        except requests.RequestException as e:
            print(f"[FACEBOOK] Batch request error: {e}")
            if attempt < MAX_RETRIES:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                time.sleep(wait)
                continue
            raise Exception(
                f"[FACEBOOK] Batch request failed after {MAX_RETRIES} retries: {e}"
            )

        if isinstance(results, dict) and "error" in results:
            code = results["error"].get("code", 0)
            msg = results["error"].get("message", "")
            if code in RATE_LIMIT_CODES:
                if attempt < MAX_RETRIES:
                    wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                    time.sleep(wait)
                    continue
                raise Exception(
                    f"[FACEBOOK] Batch rate limit exceeded after {MAX_RETRIES} retries."
                )
            if code == 190:
                raise Exception(
                    "[FACEBOOK] Invalid or expired token. "
                    "Facebook Pages requires a Page Access Token (not the Ads token). "
                    "Generate one via /me/accounts with pages_read_engagement permission."
                )
            raise Exception(f"[FACEBOOK] Batch error: {msg}")

        return results

    raise Exception("[FACEBOOK] Batch max retries exceeded")


def _fetch_page_batch(endpoint, page_id, access_token, cursor=None, limit=50):
    fields_core = (
        "id,message,created_time,permalink_url,story,from,"
        "full_picture,status_type,is_published,updated_time"
    )
    fields_engagement = (
        "reactions.type(LIKE).limit(0).summary(total_count).as(r_like),"
        "reactions.type(LOVE).limit(0).summary(total_count).as(r_love),"
        "reactions.type(WOW).limit(0).summary(total_count).as(r_wow),"
        "reactions.type(HAHA).limit(0).summary(total_count).as(r_haha),"
        "reactions.type(SAD).limit(0).summary(total_count).as(r_sad),"
        "reactions.type(ANGRY).limit(0).summary(total_count).as(r_angry),"
        "likes.summary(true),comments.summary(true),shares"
    )

    after = f"&after={cursor}" if cursor else ""

    batch = [
        {
            "method": "GET",
            "relative_url": (
                f"{page_id}/{endpoint}?fields={fields_core}&limit={limit}{after}"
            ),
        },
        {
            "method": "GET",
            "relative_url": (
                f"{page_id}/{endpoint}?fields={fields_engagement}&limit={limit}{after}"
            ),
        },
    ]

    results = _execute_batch(batch, access_token)

    if not isinstance(results, list) or len(results) < 2:
        raise Exception("[FACEBOOK] Batch returned unexpected format")

    data_list = []
    for i, r in enumerate(results):
        if not isinstance(r, dict):
            raise Exception(f"[FACEBOOK] Batch sub-request {i}: unexpected type")
        if r.get("code") != 200:
            body = r.get("body", "")
            raise Exception(
                f"[FACEBOOK] Batch sub-request {i} failed: code={r['code']}, body={body}"
            )
        try:
            data_list.append(json.loads(r["body"]))
        except (KeyError, json.JSONDecodeError) as e:
            raise Exception(f"[FACEBOOK] Batch sub-request {i} parse error: {e}")

    data_core, data_engagement = data_list

    posts_by_id = {}
    for post in data_core.get("data", []):
        pid = post.get("id")
        if pid:
            posts_by_id[pid] = post
    for post in data_engagement.get("data", []):
        pid = post.get("id")
        if pid and pid in posts_by_id:
            posts_by_id[pid].update(post)

    paging = data_core.get("paging", {})
    next_cursor = None
    next_url = paging.get("next")
    if next_url:
        m = re.search(r"after=([^&]+)", next_url)
        if m:
            next_cursor = m.group(1)

    return list(posts_by_id.values()), next_cursor


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
            # HTTP 500 - reduce data payload
            if status == 500 and "reduce the amount of data" in error_msg.lower():
                if attempt < retries:
                    current_limit = (params or {}).get("limit", 50)
                    if isinstance(current_limit, (int, str)):
                        try:
                            new_limit = max(int(current_limit) // 2, 5)
                            if params:
                                params["limit"] = new_limit
                            print(
                                f"[FACEBOOK] Reducing limit to {new_limit} and retrying "
                                f"({context})..."
                            )
                            time.sleep(2)
                            continue
                        except (ValueError, TypeError):
                            pass
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


def _get_reaction(post: dict, name: str) -> int:
    """Extract reaction count from nested or flat API response."""
    val = post.get(name, {})
    if isinstance(val, dict):
        return val.get("summary", {}).get("total_count", 0) or 0
    return val or 0


@dlt.resource(name="page_posts", write_disposition="replace")
def get_page_posts(page_id: str, access_token: str):
    cursor = None

    while True:
        posts, next_cursor = _fetch_page_batch("posts", page_id, access_token, cursor)

        for post in posts:
            yield {
                "post_id": post.get("id"),
                "message": post.get("message"),
                "created_time": post.get("created_time"),
                "permalink_url": post.get("permalink_url"),
                "story": post.get("story"),
                "full_picture": post.get("full_picture"),
                "r_like": _get_reaction(post, "r_like"),
                "r_love": _get_reaction(post, "r_love"),
                "r_wow": _get_reaction(post, "r_wow"),
                "r_haha": _get_reaction(post, "r_haha"),
                "r_sad": _get_reaction(post, "r_sad"),
                "r_angry": _get_reaction(post, "r_angry"),
                "status_type": post.get("status_type"),
                "is_published": post.get("is_published"),
                "updated_time": post.get("updated_time"),
                "likes_count": post.get("likes", {}).get("summary", {}).get("total_count", 0) or 0,
                "comments_count": post.get("comments", {}).get("summary", {}).get("total_count", 0)
                or 0,
                "shares_count": (post.get("shares") or {}).get("count", 0) or 0,
            }

        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        time.sleep(0.3)


@dlt.resource(name="feed", write_disposition="replace")
def get_page_feed(page_id: str, access_token: str):
    cursor = None

    while True:
        posts, next_cursor = _fetch_page_batch("feed", page_id, access_token, cursor)

        for item in posts:
            author = item.get("from") or {}
            yield {
                "feed_item_id": item.get("id"),
                "message": item.get("message"),
                "created_time": item.get("created_time"),
                "permalink_url": item.get("permalink_url"),
                "story": item.get("story"),
                "author_id": author.get("id"),
                "author_name": author.get("name"),
                "full_picture": item.get("full_picture"),
                "r_like": _get_reaction(item, "r_like"),
                "r_love": _get_reaction(item, "r_love"),
                "r_wow": _get_reaction(item, "r_wow"),
                "r_haha": _get_reaction(item, "r_haha"),
                "r_sad": _get_reaction(item, "r_sad"),
                "r_angry": _get_reaction(item, "r_angry"),
                "status_type": item.get("status_type"),
                "is_published": item.get("is_published"),
                "updated_time": item.get("updated_time"),
                "likes_count": item.get("likes", {}).get("summary", {}).get("total_count", 0) or 0,
                "comments_count": item.get("comments", {}).get("summary", {}).get("total_count", 0)
                or 0,
                "shares_count": (item.get("shares") or {}).get("count", 0) or 0,
            }

        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

        time.sleep(0.3)


MAX_FACEBOOK_WINDOW_DAYS = 90  # API limit: 90 days between since and until


@dlt.resource(name="page_insights_daily", write_disposition="replace")
def get_page_insights(page_id: str, access_token: str, insights_days_back: int = 729):
    metrics = [
        "page_total_media_view_unique",
        "page_media_view",
        "page_video_views",
        "page_views_total",
        "page_daily_follows",
        "page_total_actions",
        "page_follows",
        "page_post_engagements",
        "page_daily_follows_unique",
        "page_daily_unfollows_unique",
        "page_actions_post_reactions_like_total",
        "page_actions_post_reactions_love_total",
        "page_actions_post_reactions_wow_total",
        "page_actions_post_reactions_haha_total",
        "page_actions_post_reactions_sorry_total",
        "page_actions_post_reactions_anger_total",
    ]

    now = datetime.now(timezone.utc)
    until_dt = now
    since_dt = now - timedelta(days=insights_days_back)

    url = f"{FACEBOOK_API_BASE}/{page_id}/insights"

    all_metric_values: dict[str, Any] = {}

    window_start = since_dt
    while window_start < until_dt:
        window_end = min(window_start + timedelta(days=MAX_FACEBOOK_WINDOW_DAYS), until_dt)
        since_ts = int(window_start.timestamp())
        until_ts = int(window_end.timestamp())

        params = {
            "metric": ",".join(metrics),
            "period": "day",
            "since": since_ts,
            "until": until_ts,
            "access_token": access_token,
        }

        data = _do_request(url, params, f"page {page_id} insights s={since_ts}")

        for insight in data.get("data", []):
            metric_name = insight.get("name")
            for value in insight.get("values", []):
                date = (value.get("end_time") or "")[:10]
                if not date:
                    continue
                all_metric_values.setdefault(date, {})[metric_name] = value.get("value")

        window_start = window_end

    for date, vals in sorted(all_metric_values.items()):
        yield {
            "report_date": date,
            "page_total_media_view_unique": vals.get("page_total_media_view_unique"),
            "page_media_view": vals.get("page_media_view"),
            "page_video_views": vals.get("page_video_views"),
            "page_views_total": vals.get("page_views_total"),
            "page_daily_follows": vals.get("page_daily_follows"),
            "page_total_actions": vals.get("page_total_actions"),
            "page_follows": vals.get("page_follows"),
            "page_post_engagements": vals.get("page_post_engagements"),
            "page_daily_follows_unique": vals.get("page_daily_follows_unique"),
            "page_daily_unfollows_unique": vals.get("page_daily_unfollows_unique"),
            "page_actions_post_reactions_like_total": vals.get(
                "page_actions_post_reactions_like_total"
            ),
            "page_actions_post_reactions_love_total": vals.get(
                "page_actions_post_reactions_love_total"
            ),
            "page_actions_post_reactions_wow_total": vals.get(
                "page_actions_post_reactions_wow_total"
            ),
            "page_actions_post_reactions_haha_total": vals.get(
                "page_actions_post_reactions_haha_total"
            ),
            "page_actions_post_reactions_sorry_total": vals.get(
                "page_actions_post_reactions_sorry_total"
            ),
            "page_actions_post_reactions_anger_total": vals.get(
                "page_actions_post_reactions_anger_total"
            ),
        }


@dlt.resource(name="page_profile", write_disposition="replace")
def get_page_profile(page_id: str, access_token: str):
    state = dlt.current.resource_state()
    today = datetime.now(timezone.utc).date().isoformat()
    if state.get("last_run") == today:
        return

    url = f"{FACEBOOK_API_BASE}/{page_id}"
    params: dict[str, Any] = {
        "fields": (
            "fan_count,followers_count,name,username,"
            "picture.type(large){url},about,website,"
            "verification_status,rating_count,category,cover"
        ),
        "access_token": access_token,
    }
    data = _do_request(url, params, f"page {page_id} profile")

    yield {
        "page_id": data.get("id"),
        "fan_count": data.get("fan_count"),
        "followers_count": data.get("followers_count"),
        "name": data.get("name"),
        "username": data.get("username"),
        "picture_url": (data.get("picture") or {}).get("url"),
        "about": data.get("about"),
        "website": data.get("website"),
        "verification_status": data.get("verification_status"),
        "rating_count": data.get("rating_count"),
        "category": data.get("category"),
        "cover": (data.get("cover") or {}).get("source"),
    }

    state["last_run"] = today


@dlt.source
def facebook_page_source(page_id: str, access_token: str, insights_days_back: int = 729):
    return [
        get_page_posts(page_id, access_token),
        get_page_feed(page_id, access_token),
        get_page_insights(page_id, access_token, insights_days_back),
        get_page_profile(page_id, access_token),
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
    insights_days_back = connector.get("insights_days_back", 729)

    info = pipeline.run(facebook_page_source(page_id, access_token, insights_days_back))
    print(f"[FACEBOOK] Done: {info}")
