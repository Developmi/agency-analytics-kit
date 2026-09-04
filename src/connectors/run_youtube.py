from __future__ import annotations

import argparse
import os
import re
import time
from datetime import date, timedelta
from typing import Any

import dlt
import yaml
from dlt.sources.helpers import requests

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
YOUTUBE_ANALYTICS_BASE = "https://youtubeanalytics.googleapis.com/v2/reports"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"

TOKEN_CACHE_SECONDS = 3300  # ~55 minutes, well below the 1h access token lifetime
ANALYTICS_WINDOW_DAYS = 350  # max safe date range per Analytics query
DEFAULT_REGION_CODE = "CO"
DEFAULT_ANALYTICS_START_DATE = "2016-08-08"

DAILY_ANALYTICS_METRICS = (
    "views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage,"
    "subscribersGained,subscribersLost,likes,dislikes,comments,shares,engagedViews"
)
VIDEO_ANALYTICS_METRICS = "views,estimatedMinutesWatched,likes,comments,shares,averageViewDuration"
DIMENSION_ANALYTICS_METRICS = "views,estimatedMinutesWatched"


class YouTubeAuthError(Exception):
    """Raised when the YouTube API rejects the current access token (HTTP 401)."""


def _parse_iso_duration(duration: str) -> int:
    match = re.match(r"^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration)
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


class _TokenManager:
    """Cached OAuth access token obtained from a refresh token (stdlib urllib/requests only)."""

    def __init__(self, client_id: str, client_secret: str, refresh_token: str):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._access_token: str | None = None
        self._expires_at = 0.0

    def get_token(self) -> str:
        if self._access_token and time.time() < self._expires_at:
            return self._access_token
        return self._refresh()

    def invalidate(self) -> None:
        self._access_token = None
        self._expires_at = 0.0

    def _refresh(self) -> str:
        data = {
            "grant_type": "refresh_token",
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
        }
        try:
            response = requests.post(TOKEN_ENDPOINT, data=data)
        except requests.RequestException as e:
            raise Exception(f"[YOUTUBE] Token request error: {e}")
        payload = response.json()
        access_token = payload.get("access_token")
        if not access_token:
            error = payload.get("error", "unknown error")
            raise Exception(f"[YOUTUBE] Token refresh failed: {error}")
        self._access_token = access_token
        expires_in = int(payload.get("expires_in", 3600))
        self._expires_at = time.time() + expires_in - 300
        return self._access_token


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
        if code == 401:
            raise YouTubeAuthError(msg)
        elif code == 403 and "quotaExceeded" in msg:
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


def _api_get(
    url: str,
    params: dict[str, Any],
    context: str,
    api_key: str | None = None,
    token_manager: _TokenManager | None = None,
):
    """GET helper with quota/429 backoff and a single 401 -> refresh -> retry path.

    Public endpoints authenticate with ``key``, OAuth-only endpoints with a Bearer token.
    """
    while True:
        headers = {}
        if token_manager is not None:
            headers["Authorization"] = f"Bearer {token_manager.get_token()}"
            request_params = dict(params)
        elif api_key:
            request_params = {**params, "key": api_key}
        else:
            request_params = dict(params)
        try:
            response = requests.get(url, params=request_params, headers=headers)
        except requests.HTTPError as e:
            # dlt's requests wrapper raises on non-2xx; the error body is still
            # JSON (quota, rate limit, auth, bad request...), so reuse it.
            if e.response is None:
                raise
            response = e.response
        except requests.RequestException as e:
            print(f"[YOUTUBE] Request error ({context}): {e}")
            raise
        try:
            data = _handle_response(response, context)
        except YouTubeAuthError:
            if token_manager is None:
                raise
            print(f"[YOUTUBE] Access token rejected ({context}). Refreshing token and retrying...")
            token_manager.invalidate()
            continue
        if data is None:
            continue
        return data


def _snake_case(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _is_empty_analytics_error(error: Exception) -> bool:
    """Analytics returns 400 with 'no data' messages for windows the channel has no data in."""
    message = str(error).lower()
    return "no data" in message or "did not return any data" in message


def _date_windows(start_date: str, end_date: str, window_days: int = ANALYTICS_WINDOW_DAYS):
    """Yield (start, end) ISO date pairs covering [start_date, end_date] in <=350-day chunks."""
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    while start <= end:
        window_end = min(start + timedelta(days=window_days - 1), end)
        yield start.isoformat(), window_end.isoformat()
        start = window_end + timedelta(days=1)


def _get_uploads_playlist_id(channel_id: str, api_key: str) -> str:
    """Read contentDetails.relatedPlaylists.uploads for the channel, with UU fallback."""
    url = f"{YOUTUBE_API_BASE}/channels"
    params: dict[str, Any] = {"part": "contentDetails", "id": channel_id}
    data = _api_get(url, params, "uploads playlist lookup", api_key=api_key)
    items = data.get("items", [])
    if items:
        uploads = items[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
        if uploads:
            return uploads
    fallback = f"UU{channel_id[2:]}"
    print(f"[YOUTUBE] Uploads playlist not found, using fallback {fallback}")
    return fallback


def _enumerate_uploads(
    channel_id: str,
    uploads_playlist_id: str,
    api_key: str | None = None,
    token_manager: _TokenManager | None = None,
) -> list[str]:
    """Enumerate upload video ids via playlistItems.list (replaces search.list)."""
    url = f"{YOUTUBE_API_BASE}/playlistItems"
    params: dict[str, Any] = {
        "part": "contentDetails",
        "playlistId": uploads_playlist_id,
        "maxResults": 50,
    }
    video_ids: list[str] = []
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        data = _api_get(
            url,
            params,
            "uploads enumeration",
            api_key=api_key,
            token_manager=token_manager,
        )
        for item in data.get("items", []):
            video_id = item.get("contentDetails", {}).get("videoId")
            if video_id:
                video_ids.append(video_id)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    if not video_ids:
        print(f"[YOUTUBE] No videos found for channel {channel_id}")
    return video_ids


def _analytics_query(
    token_manager: _TokenManager,
    channel_id: str,
    start_date: str,
    end_date: str,
    dimensions: str,
    metrics: str,
    context: str,
    sort: str | None = None,
    max_results: int = 10000,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "ids": "channel==MINE",
        "startDate": start_date,
        "endDate": end_date,
        "metrics": metrics,
        "dimensions": dimensions,
        "maxResults": max_results,
    }
    if sort:
        params["sort"] = sort
    data = _api_get(
        YOUTUBE_ANALYTICS_BASE,
        params,
        context,
        token_manager=token_manager,
    )
    headers = data.get("columnHeaders", [])
    rows: list[dict[str, Any]] = []
    for row in data.get("rows", []):
        record: dict[str, Any] = {}
        for header, value in zip(headers, row):
            name = _snake_case(header.get("name", ""))
            if name == "day":
                name = "report_date"
            elif name == "video":
                name = "video_id"
            record[name] = value
        rows.append(record)
    return rows


@dlt.resource(name="channel_stats", write_disposition="replace")
def get_channel_stats(channel_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/channels"
    params: dict[str, Any] = {
        "part": "snippet,statistics,contentDetails,brandingSettings,topicDetails,status",
        "id": channel_id,
    }
    data = _api_get(url, params, "channel_stats", api_key=api_key)
    items = data.get("items", [])
    if not items:
        print(f"[YOUTUBE] No channel found for id {channel_id}")
        return

    item = items[0]
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    status = item.get("status", {})
    yield {
        "report_date": date.today().isoformat(),
        "subscriber_count": int(stats.get("subscriberCount", 0)),
        "view_count": int(stats.get("viewCount", 0)),
        "video_count": int(stats.get("videoCount", 0)),
        "hidden_subscriber_count": stats.get("hiddenSubscriberCount", False),
        "uploads_playlist_id": item.get("contentDetails", {})
        .get("relatedPlaylists", {})
        .get("uploads"),
        "title": snippet.get("title"),
        "description": snippet.get("description"),
        "custom_url": snippet.get("customUrl"),
        "published_at": snippet.get("publishedAt"),
        "country": snippet.get("country"),
        "default_language": snippet.get("defaultLanguage"),
        "keywords": item.get("brandingSettings", {}).get("channel", {}).get("keywords"),
        "topic_categories": item.get("topicDetails", {}).get("topicCategories", []),
        "privacy_status": status.get("privacyStatus"),
        "is_linked": status.get("isLinked"),
        "long_uploads_status": status.get("longUploadsStatus"),
        "made_for_kids": status.get("madeForKids"),
    }


@dlt.resource(name="uploaded_videos", write_disposition="replace")
def get_uploaded_videos(channel_id: str, uploads_playlist_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/playlistItems"
    params: dict[str, Any] = {
        "part": "snippet,contentDetails",
        "playlistId": uploads_playlist_id,
        "maxResults": 50,
    }
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        data = _api_get(url, params, "uploaded_videos", api_key=api_key)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            yield {
                "playlist_item_id": item.get("id"),
                "playlist_id": snippet.get("playlistId"),
                "video_id": item.get("contentDetails", {}).get("videoId"),
                "position": snippet.get("position"),
                "video_published_at": snippet.get("videoPublishedAt"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "thumbnails": snippet.get("thumbnails"),
                "channel_id": channel_id,
            }
        page_token = data.get("nextPageToken")
        if not page_token:
            break


@dlt.resource(name="videos", write_disposition="replace")
def get_videos(channel_id: str, uploads_playlist_id: str, api_key: str):
    video_ids = _enumerate_uploads(channel_id, uploads_playlist_id, api_key)
    if not video_ids:
        return

    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        stats_url = f"{YOUTUBE_API_BASE}/videos"
        stats_params: dict[str, Any] = {
            "part": "snippet,statistics,contentDetails,status,topicDetails,liveStreamingDetails",
            "id": ",".join(batch),
        }
        stats_data = _api_get(stats_url, stats_params, "videos stats", api_key=api_key)

        for item in stats_data.get("items", []):
            snippet = item.get("snippet", {})
            stat = item.get("statistics", {})
            content_details = item.get("contentDetails", {})
            status = item.get("status", {})
            live = item.get("liveStreamingDetails", {})
            yield {
                "video_id": item["id"],
                "title": snippet.get("title"),
                "published_at": snippet.get("publishedAt"),
                "view_count": int(stat.get("viewCount", 0)),
                "like_count": int(stat.get("likeCount", 0)),
                "dislike_count": int(stat.get("dislikeCount", 0)),
                "comment_count": int(stat.get("commentCount", 0)),
                "duration": content_details.get("duration"),
                "category_id": snippet.get("categoryId"),
                "description": snippet.get("description"),
                "tags": snippet.get("tags", []),
                "thumbnail_url": snippet.get("thumbnails", {}).get("default", {}).get("url"),
                "privacy_status": status.get("privacyStatus"),
                "license": status.get("license"),
                "embeddable": status.get("embeddable"),
                "public_stats_viewable": status.get("publicStatsViewable"),
                "made_for_kids": status.get("madeForKids"),
                "self_declared_made_for_kids": status.get("selfDeclaredMadeForKids"),
                "topic_categories": item.get("topicDetails", {}).get("topicCategories", []),
                "live_broadcast_content": content_details.get("liveBroadcastContent"),
                "live_scheduled_start_time": live.get("scheduledStartTime"),
                "live_actual_start_time": live.get("actualStartTime"),
                "live_actual_end_time": live.get("actualEndTime"),
                "live_concurrent_viewers": live.get("concurrentViewers"),
            }


@dlt.resource(name="playlists", write_disposition="replace")
def get_playlists(channel_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/playlists"
    params: dict[str, Any] = {
        "part": "snippet,contentDetails,status",
        "channelId": channel_id,
        "maxResults": 50,
    }
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        data = _api_get(url, params, "playlists", api_key=api_key)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            yield {
                "playlist_id": item.get("id"),
                "channel_id": snippet.get("channelId"),
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "published_at": snippet.get("publishedAt"),
                "updated_at": snippet.get("updatedAt"),
                "item_count": item.get("contentDetails", {}).get("itemCount"),
                "privacy_status": item.get("status", {}).get("privacyStatus"),
            }
        page_token = data.get("nextPageToken")
        if not page_token:
            break


@dlt.resource(name="playlist_items", write_disposition="replace")
def get_playlist_items(channel_id: str, uploads_playlist_id: str, api_key: str):
    playlist_ids = [row["playlist_id"] for row in get_playlists(channel_id, api_key)]
    if not playlist_ids:
        print("[YOUTUBE] No playlists found, enumerating only uploads playlist")
        playlist_ids = [uploads_playlist_id]

    for playlist_id in playlist_ids:
        url = f"{YOUTUBE_API_BASE}/playlistItems"
        params: dict[str, Any] = {
            "part": "snippet,contentDetails,status",
            "playlistId": playlist_id,
            "maxResults": 50,
        }
        page_token = None
        while True:
            if page_token:
                params["pageToken"] = page_token
            data = _api_get(url, params, f"playlist_items {playlist_id}", api_key=api_key)
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                yield {
                    "playlist_item_id": item.get("id"),
                    "playlist_id": snippet.get("playlistId"),
                    "video_id": item.get("contentDetails", {}).get("videoId"),
                    "position": snippet.get("position"),
                    "title": snippet.get("title"),
                    "description": snippet.get("description"),
                    "published_at": snippet.get("publishedAt"),
                    "video_published_at": snippet.get("videoPublishedAt"),
                    "privacy_status": item.get("status", {}).get("privacyStatus"),
                    "channel_id": channel_id,
                }
            page_token = data.get("nextPageToken")
            if not page_token:
                break


@dlt.resource(name="comment_threads", write_disposition="replace")
def get_comment_threads(channel_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/commentThreads"
    params: dict[str, Any] = {
        "part": "snippet,replies",
        "allThreadsRelatedToChannelId": channel_id,
        "maxResults": 100,
    }
    page_token = None
    while True:
        if page_token:
            params["pageToken"] = page_token
        data = _api_get(url, params, "comment_threads", api_key=api_key)
        for item in data.get("items", []):
            thread_snippet = item.get("snippet", {})
            top = thread_snippet.get("topLevelComment", {}).get("snippet", {})
            author_channel = top.get("authorChannelId", {}) or {}
            replies = []
            for reply in item.get("replies", {}).get("comments", []):
                reply_snippet = reply.get("snippet", {})
                reply_author = reply_snippet.get("authorChannelId", {}) or {}
                replies.append(
                    {
                        "comment_id": reply.get("id"),
                        "author_display_name": reply_snippet.get("authorDisplayName"),
                        "author_channel_id": reply_author.get("value"),
                        "author_channel_url": reply_snippet.get("authorChannelUrl"),
                        "text_original": reply_snippet.get("textOriginal"),
                        "like_count": reply_snippet.get("likeCount"),
                        "published_at": reply_snippet.get("publishedAt"),
                        "updated_at": reply_snippet.get("updatedAt"),
                    }
                )
            yield {
                "comment_id": item.get("id"),
                "video_id": thread_snippet.get("videoId"),
                "channel_id": channel_id,
                "author_display_name": top.get("authorDisplayName"),
                "author_channel_id": author_channel.get("value"),
                "author_channel_url": top.get("authorChannelUrl"),
                "text_original": top.get("textOriginal"),
                "like_count": top.get("likeCount"),
                "published_at": top.get("publishedAt"),
                "updated_at": top.get("updatedAt"),
                "total_reply_count": thread_snippet.get("totalReplyCount"),
                "is_public": thread_snippet.get("isPublic"),
                "can_reply": thread_snippet.get("canReply"),
                "replies": replies,
            }
        page_token = data.get("nextPageToken")
        if not page_token:
            break


@dlt.resource(name="channel_sections", write_disposition="replace")
def get_channel_sections(channel_id: str, api_key: str):
    url = f"{YOUTUBE_API_BASE}/channelSections"
    params: dict[str, Any] = {
        "part": "snippet,contentDetails",
        "channelId": channel_id,
    }
    data = _api_get(url, params, "channel_sections", api_key=api_key)
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        content_details = item.get("contentDetails", {})
        yield {
            "section_id": item.get("id"),
            "channel_id": channel_id,
            "type": snippet.get("type"),
            "title": snippet.get("title"),
            "position": snippet.get("position"),
            "playlist_ids": content_details.get("playlists", []),
            "channel_ids": content_details.get("channels", []),
        }


@dlt.resource(name="video_categories", write_disposition="replace")
def get_video_categories(api_key: str, region_code: str = DEFAULT_REGION_CODE):
    url = f"{YOUTUBE_API_BASE}/videoCategories"
    params: dict[str, Any] = {"part": "snippet", "regionCode": region_code}
    data = _api_get(url, params, "video_categories", api_key=api_key)
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        yield {
            "category_id": item.get("id"),
            "title": snippet.get("title"),
            "assignable": snippet.get("assignable"),
            "channel_id": snippet.get("channelId"),
        }


@dlt.resource(name="captions", write_disposition="replace")
def get_captions(channel_id: str, uploads_playlist_id: str, token_manager: _TokenManager):
    video_ids = _enumerate_uploads(channel_id, uploads_playlist_id, token_manager=token_manager)
    if not video_ids:
        return
    url = f"{YOUTUBE_API_BASE}/captions"
    for video_id in video_ids:
        params: dict[str, Any] = {"part": "snippet", "videoId": video_id}
        data = _api_get(url, params, f"captions {video_id}", token_manager=token_manager)
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            yield {
                "caption_id": item.get("id"),
                "video_id": snippet.get("videoId"),
                "language": snippet.get("language"),
                "name": snippet.get("name"),
                "track_kind": snippet.get("trackKind"),
                "status": snippet.get("status"),
                "last_updated": snippet.get("lastUpdated"),
                "is_cc": snippet.get("isCC"),
                "is_draft": snippet.get("isDraft"),
                "is_auto_synced": snippet.get("isAutoSynced"),
            }


@dlt.resource(name="video_daily_analytics", write_disposition="replace")
def get_video_daily_analytics(
    channel_id: str, token_manager: _TokenManager, start_date: str = DEFAULT_ANALYTICS_START_DATE
):
    end_date = (date.today() - timedelta(days=1)).isoformat()
    for window_start, window_end in _date_windows(start_date, end_date):
        context = f"video_daily_analytics {window_start}..{window_end}"
        try:
            rows = _analytics_query(
                token_manager,
                channel_id,
                window_start,
                window_end,
                dimensions="day",
                metrics=DAILY_ANALYTICS_METRICS,
                context=context,
            )
        except Exception as e:
            if _is_empty_analytics_error(e):
                print(f"[YOUTUBE] No analytics data in {context}. Skipping window.")
                continue
            raise
        for row in rows:
            yield row


@dlt.resource(name="video_analytics", write_disposition="replace")
def get_video_analytics(
    channel_id: str, token_manager: _TokenManager, start_date: str = DEFAULT_ANALYTICS_START_DATE
):
    end_date = (date.today() - timedelta(days=1)).isoformat()
    context = f"video_analytics {start_date}..{end_date}"
    # The video dimension report requires a sort parameter (descending, with the
    # '-' prefix) and maxResults <= 200 (covers the full channel: 79 videos).
    rows = _analytics_query(
        token_manager,
        channel_id,
        start_date,
        end_date,
        dimensions="video",
        metrics=VIDEO_ANALYTICS_METRICS,
        context=context,
        sort="-views",
        max_results=200,
    )
    for row in rows:
        yield row


def _dimension_analytics_resource(
    resource_name: str,
    channel_id: str,
    token_manager: _TokenManager,
    dimension: str,
    start_date: str,
):
    @dlt.resource(name=resource_name, write_disposition="replace")
    def resource():
        end_date = (date.today() - timedelta(days=1)).isoformat()
        for window_start, window_end in _date_windows(start_date, end_date):
            context = f"{resource_name} {window_start}..{window_end}"
            try:
                rows = _analytics_query(
                    token_manager,
                    channel_id,
                    window_start,
                    window_end,
                    dimensions=f"day,{dimension}",
                    metrics=DIMENSION_ANALYTICS_METRICS,
                    context=context,
                )
            except Exception as e:
                if _is_empty_analytics_error(e):
                    print(f"[YOUTUBE] No analytics data in {context}. Skipping window.")
                    continue
                raise
            for row in rows:
                yield row

    return resource


def get_traffic_source_analytics(
    channel_id: str, token_manager: _TokenManager, start_date: str = DEFAULT_ANALYTICS_START_DATE
):
    return _dimension_analytics_resource(
        "traffic_source_analytics",
        channel_id,
        token_manager,
        "insightTrafficSourceType",
        start_date,
    )


def get_device_analytics(
    channel_id: str, token_manager: _TokenManager, start_date: str = DEFAULT_ANALYTICS_START_DATE
):
    return _dimension_analytics_resource(
        "device_analytics", channel_id, token_manager, "deviceType", start_date
    )


@dlt.resource(name="country_analytics", write_disposition="replace")
def get_country_analytics(
    channel_id: str, token_manager: _TokenManager, start_date: str = DEFAULT_ANALYTICS_START_DATE
):
    # The Analytics API does not support day+country dimensions together;
    # per-country totals over the full backfill range are used instead.
    end_date = (date.today() - timedelta(days=1)).isoformat()
    context = f"country_analytics {start_date}..{end_date}"
    rows = _analytics_query(
        token_manager,
        channel_id,
        start_date,
        end_date,
        dimensions="country",
        metrics=DIMENSION_ANALYTICS_METRICS,
        context=context,
    )
    for row in rows:
        yield row


@dlt.source
def youtube_source(
    channel_id: str,
    api_key: str,
    oauth_config: dict[str, str] | None = None,
    captions_enabled: bool = False,
    region_code: str = DEFAULT_REGION_CODE,
    analytics_start_date: str = DEFAULT_ANALYTICS_START_DATE,
):
    uploads_playlist_id = _get_uploads_playlist_id(channel_id, api_key)
    print(f"[YOUTUBE] Using uploads playlist {uploads_playlist_id}")

    resources = [
        get_channel_stats(channel_id, api_key),
        get_uploaded_videos(channel_id, uploads_playlist_id, api_key),
        get_videos(channel_id, uploads_playlist_id, api_key),
        get_playlists(channel_id, api_key),
        get_playlist_items(channel_id, uploads_playlist_id, api_key),
        get_comment_threads(channel_id, api_key),
        get_channel_sections(channel_id, api_key),
        get_video_categories(api_key, region_code),
    ]

    if oauth_config is None:
        print(
            "[YOUTUBE] OAuth credentials missing (client_id/client_secret/refresh_token). "
            "Skipping analytics and captions resources."
        )
        return resources

    token_manager = _TokenManager(
        client_id=oauth_config["client_id"],
        client_secret=oauth_config["client_secret"],
        refresh_token=oauth_config["refresh_token"],
    )
    resources += [
        get_video_daily_analytics(channel_id, token_manager, analytics_start_date),
        get_video_analytics(channel_id, token_manager, analytics_start_date),
        get_traffic_source_analytics(channel_id, token_manager, analytics_start_date),
        get_device_analytics(channel_id, token_manager, analytics_start_date),
        get_country_analytics(channel_id, token_manager, analytics_start_date),
    ]
    if captions_enabled:
        resources.append(get_captions(channel_id, uploads_playlist_id, token_manager))
    else:
        print("[YOUTUBE] Captions disabled (captions_enabled=false). Skipping.")
    return resources


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
    api_key = os.environ.get(token_env, "")
    if not api_key:
        print(
            f"[YOUTUBE] Environment variable {token_env} is not set. "
            "Skipping YouTube for this client."
        )
        exit(0)

    oauth_config = None
    oauth_client_id = connector.get("oauth_client_id_env")
    oauth_client_secret = connector.get("oauth_client_secret_env")
    oauth_refresh_token = connector.get("oauth_refresh_token_env")
    if oauth_client_id and oauth_client_secret and oauth_refresh_token:
        oauth_config = {
            "client_id": os.environ.get(oauth_client_id, ""),
            "client_secret": os.environ.get(oauth_client_secret, ""),
            "refresh_token": os.environ.get(oauth_refresh_token, ""),
        }
        if not all(oauth_config.values()):
            print(
                "[YOUTUBE] OAuth env keys configured but missing values. "
                "Skipping analytics and captions resources."
            )
            oauth_config = None
    captions_enabled = bool(connector.get("captions_enabled", False))
    region_code = str(connector.get("region_code", DEFAULT_REGION_CODE))
    analytics_start_date = str(connector.get("analytics_start_date", DEFAULT_ANALYTICS_START_DATE))

    print(f"[YOUTUBE] Extracting data for client '{args.client}' (channel {channel_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"youtube_{args.client}",
        destination="postgres",
        dataset_name="raw_youtube",
    )
    info = pipeline.run(
        youtube_source(
            channel_id,
            api_key,
            oauth_config=oauth_config,
            captions_enabled=captions_enabled,
            region_code=region_code,
            analytics_start_date=analytics_start_date,
        )
    )
    print(f"[YOUTUBE] Done: {info}")
