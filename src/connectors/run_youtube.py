from __future__ import annotations

import argparse
import os
import re
import time
from datetime import date
from typing import Any

import dlt
import yaml
from dlt.sources.helpers import requests

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


def _parse_iso_duration(duration: str) -> int:
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def _handle_response(response, context: str):
    try:
        data = response.json()
    except requests.RequestException as e:
        print(f"[YOUTUBE] JSON parse error in {context}: {e}")
        raise

    if "error" in data:
        error = data["error"]
        code = error.get("code", 0)
        msg = error.get("message", "")
        if code == 403 and "quotaExceeded" in msg:
            wait = 60
            print(f"[YOUTUBE] Quota exceeded ({context}). Waiting {wait}s...")
            time.sleep(wait)
            return None
        elif code == 429:
            wait = int(response.headers.get("Retry-After", 30))
            print(f"[YOUTUBE] Rate limited ({context}). Waiting {wait}s...")
            time.sleep(wait)
            return None
        elif code == 400:
            raise Exception(f"[YOUTUBE] Bad request in {context}: {msg}")
        else:
            raise Exception(f"[YOUTUBE] API error {code} in {context}: {msg}")
    return data


@dlt.resource(name="channel_stats", write_disposition="replace")
def get_channel_stats(channel_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/channels"
    params: dict[str, Any] = {
        "part": "snippet,statistics",
        "id": channel_id,
        "key": api_key,
    }
    while True:
        try:
            response = requests.get(url, params=params)
        except requests.RequestException as e:
            print(f"[YOUTUBE] Request error (channel_stats): {e}")
            raise

        data = _handle_response(response, "channel_stats")
        if data is None:
            continue

        items = data.get("items", [])
        if not items:
            print(f"[YOUTUBE] No channel found for id {channel_id}")
            break

        item = items[0]
        stats = item.get("statistics", {})
        yield {
            "report_date": date.today().isoformat(),
            "subscriber_count": int(stats.get("subscriberCount", 0)),
            "view_count": int(stats.get("viewCount", 0)),
            "video_count": int(stats.get("videoCount", 0)),
            "hidden_subscriber_count": stats.get("hiddenSubscriberCount", False),
        }
        break


@dlt.resource(name="videos", write_disposition="replace")
def get_videos(channel_id: str, api_key: str):
    search_url = f"{YOUTUBE_API_BASE}/search"
    search_params: dict[str, Any] = {
        "part": "id,snippet",
        "channelId": channel_id,
        "maxResults": 50,
        "order": "date",
        "type": "video",
        "key": api_key,
    }
    page_token = None
    video_ids = []

    while True:
        if page_token:
            search_params["pageToken"] = page_token
        try:
            response = requests.get(search_url, params=search_params)
        except requests.RequestException as e:
            print(f"[YOUTUBE] Request error (videos search): {e}")
            raise

        data = _handle_response(response, "videos search")
        if data is None:
            continue

        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        print(f"[YOUTUBE] No videos found for channel {channel_id}")
        return

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        stats_url = f"{YOUTUBE_API_BASE}/videos"
        stats_params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
            "key": api_key,
        }
        while True:
            try:
                stats_resp = requests.get(stats_url, params=stats_params)
            except requests.RequestException as e:
                print(f"[YOUTUBE] Request error (videos stats): {e}")
                raise

            stats_data = _handle_response(stats_resp, "videos stats")
            if stats_data is None:
                continue

            for item in stats_data.get("items", []):
                snippet = item.get("snippet", {})
                stat = item.get("statistics", {})
                content_details = item.get("contentDetails", {})
                yield {
                    "video_id": item["id"],
                    "title": snippet.get("title"),
                    "published_at": snippet.get("publishedAt"),
                    "view_count": int(stat.get("viewCount", 0)),
                    "like_count": int(stat.get("likeCount", 0)),
                    "comment_count": int(stat.get("commentCount", 0)),
                    "duration": content_details.get("duration"),
                    "category_id": snippet.get("categoryId"),
                }
            break


@dlt.resource(name="video_daily_analytics", write_disposition="replace")
def get_video_daily_analytics(channel_id: str, api_key: str):
    search_url = f"{YOUTUBE_API_BASE}/search"
    search_params: dict[str, Any] = {
        "part": "id",
        "channelId": channel_id,
        "maxResults": 50,
        "order": "date",
        "type": "video",
        "key": api_key,
    }
    page_token = None
    video_ids = []

    while True:
        if page_token:
            search_params["pageToken"] = page_token
        try:
            response = requests.get(search_url, params=search_params)
        except requests.RequestException as e:
            print(f"[YOUTUBE] Request error (daily analytics search): {e}")
            raise

        data = _handle_response(response, "daily analytics search")
        if data is None:
            continue

        for item in data.get("items", []):
            vid = item.get("id", {}).get("videoId")
            if vid:
                video_ids.append(vid)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

    if not video_ids:
        return

    for vid in video_ids:
        stats_url = f"{YOUTUBE_API_BASE}/videos"
        stats_params = {
            "part": "statistics,contentDetails",
            "id": vid,
            "key": api_key,
        }
        while True:
            try:
                stats_resp = requests.get(stats_url, params=stats_params)
            except requests.RequestException as e:
                print(f"[YOUTUBE] Request error (daily analytics stats): {e}")
                raise

            stats_data = _handle_response(stats_resp, "daily analytics stats")
            if stats_data is None:
                continue

            items = stats_data.get("items", [])
            if not items:
                break

            stat = items[0].get("statistics", {})
            content_details = items[0].get("contentDetails", {})
            duration_str = content_details.get("duration", "PT0S")
            estimated_seconds = _parse_iso_duration(duration_str)
            yield {
                "report_date": date.today().isoformat(),
                "video_id": vid,
                "views": int(stat.get("viewCount", 0)),
                "estimated_minutes_watched": round(estimated_seconds / 60, 2),
                "average_view_duration_seconds": round(estimated_seconds, 2),
            }
            break


@dlt.source
def youtube_source(channel_id: str, api_key: str):
    return [
        get_channel_stats(channel_id, api_key),
        get_videos(channel_id, api_key),
        get_video_daily_analytics(channel_id, api_key),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="YouTube Data API dlt extractor")
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
        print(f"[YOUTUBE] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[YOUTUBE] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("youtube", {})
    if not connector.get("enabled"):
        print(f"[YOUTUBE] YouTube connector not enabled for client {args.client}. Skipping.")
        exit(0)

    channel_id = connector["channel_id"]
    token_env = connector["token_env"]
    api_key = os.environ[token_env]

    print(f"[YOUTUBE] Extracting data for client '{args.client}' (channel {channel_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"youtube_{args.client}",
        destination="postgres",
        dataset_name="raw_youtube",
    )
    info = pipeline.run(youtube_source(channel_id, api_key))
    print(f"[YOUTUBE] Done: {info}")
