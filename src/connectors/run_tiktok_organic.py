import argparse
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

TIKTOK_API_BASE = "https://open-api.tiktok.com"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120


def _check_error(data, context):
    if not data.get("error"):
        return None
    err = data["error"]
    code = err.get("code", "")
    msg = err.get("message", "")
    if code in ("rate_limit", 40004):
        return 30
    elif code in ("access_token_expired", 40007):
        raise Exception(
            f"[TIKTOK_ORGANIC] Token expired for {context}. Renew the token in .env and redeploy."
        )
    else:
        raise Exception(f"[TIKTOK_ORGANIC] API error {code} ({context}): {msg}")


def _do_request(url, params, headers, context, retries=MAX_RETRIES):
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, params=params, headers=headers)
            data = response.json()
        except requests.RequestException as e:
            print(f"[TIKTOK_ORGANIC] Request error ({context}): {e}")
            if attempt < retries:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(f"[TIKTOK_ORGANIC] Retrying in {wait}s (attempt {attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
            raise

        wait = _check_error(data, context)
        if wait:
            if attempt < retries:
                w = min(wait, BACKOFF_MAX)
                print(f"[TIKTOK_ORGANIC] Rate limited ({context}). Retrying in {w}s...")
                time.sleep(w)
                continue
            raise Exception(
                f"[TIKTOK_ORGANIC] Rate limit exceeded after {retries} retries ({context})."
            )
        return data

    raise Exception(f"[TIKTOK_ORGANIC] Max retries exceeded ({context}).")


@dlt.resource(name="profile_stats", write_disposition="replace")
def get_profile_stats(open_id: str, access_token: str):
    url = f"{TIKTOK_API_BASE}/v2/user/info/"
    params = {
        "fields": "follower_count,following_count,likes_count,video_count",
    }
    headers = {
        "Access-Token": access_token,
    }

    data = _do_request(url, params, headers, f"open_id {open_id} profile")

    user = data.get("data", {}).get("user", {})
    total_videos = int(user.get("video_count", 0) or 0)
    total_likes = int(user.get("likes_count", 0) or 0)
    yield {
        "report_date": time.strftime("%Y-%m-%d"),
        "follower_count": int(user.get("follower_count", 0) or 0),
        "following_count": int(user.get("following_count", 0) or 0),
        "total_likes": total_likes,
        "total_videos": total_videos,
    }


@dlt.resource(name="videos_organic", write_disposition="replace")
def get_videos_organic(open_id: str, access_token: str):
    url = f"{TIKTOK_API_BASE}/v2/video/list/"
    params = {
        "fields": "id,title,create_time,like_count,comment_count,share_count,view_count",
        "max_count": 50,
    }
    headers = {
        "Access-Token": access_token,
    }
    cursor = 0
    has_more = True

    while has_more:
        params["cursor"] = cursor
        data = _do_request(url, params, headers, f"open_id {open_id} videos")

        result = data.get("data", {})
        for video in result.get("videos", []):
            yield {
                "video_id": video.get("id"),
                "title": video.get("title"),
                "create_time": video.get("create_time"),
                "like_count": int(video.get("like_count", 0) or 0),
                "comment_count": int(video.get("comment_count", 0) or 0),
                "share_count": int(video.get("share_count", 0) or 0),
                "view_count": int(video.get("view_count", 0) or 0),
            }

        cursor = result.get("cursor", 0)
        has_more = result.get("has_more", False)


@dlt.source
def tiktok_organic_source(open_id: str, access_token: str):
    return [
        get_profile_stats(open_id, access_token),
        get_videos_organic(open_id, access_token),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="TikTok Organic dlt extractor")
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
        print(f"[TIKTOK_ORGANIC] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[TIKTOK_ORGANIC] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("tiktok_organic", {})
    if not connector.get("enabled"):
        print(
            "[TIKTOK_ORGANIC] TikTok Organic connector"
            f" not enabled for client {args.client}. Skipping."
        )
        exit(0)

    open_id = connector["open_id"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[TIKTOK_ORGANIC] Extracting data for client '{args.client}' (open_id {open_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"tiktok_organic_{args.client}",
        destination="postgres",
        dataset_name="raw_tiktok_organic",
    )
    info = pipeline.run(tiktok_organic_source(open_id, access_token))
    print(f"[TIKTOK_ORGANIC] Done: {info}")
