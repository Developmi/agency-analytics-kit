import argparse
import os
import time
from datetime import datetime, timezone
from typing import Any

import dlt
from dlt.sources.helpers import requests

from agency_analytics import archiver, client_config

TIKTOK_API_BASE = "https://open.tiktokapis.com"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120


def _refresh_access_token(client_key: str, client_secret: str, refresh_token: str) -> dict:
    url = f"{TIKTOK_API_BASE}/v2/oauth/token/"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "refresh_token",
        "client_key": client_key,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }
    response = requests.post(url, headers=headers, data=data, timeout=30)
    result = response.json()

    if "error" in result:
        raise Exception(
            f"[TIKTOK_ORGANIC] Token refresh failed: {result.get('error', 'unknown')}"
            f" — {result.get('error_description', '')}"
        )

    return result


def _check_error(data: dict, context: str) -> int | None:
    if not data.get("error"):
        return None
    err = data["error"]
    code = err.get("code", "")
    msg = err.get("message", "")

    # TikTok siempre devuelve un bloque error incluso en éxito (code="ok")
    if code == "ok":
        return None
    if code in ("rate_limit", 40004):
        return 30
    elif code in ("access_token_expired", 40007, 401):
        print(f"[TIKTOK_ORGANIC] Token expired for {context}. Retrying...")
        return 5
    else:
        raise Exception(f"[TIKTOK_ORGANIC] API error {code} ({context}): {msg}")


def _do_request(
    method: str,
    url: str,
    access_token: str,
    json_body: dict | None = None,
    params: dict | None = None,
    context: str = "",
) -> dict:
    headers = {"Authorization": f"Bearer {access_token}"}
    if method == "POST":
        headers["Content-Type"] = "application/json"

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.request(
                method, url, headers=headers, json=json_body, params=params, timeout=30
            )
            data = response.json()
        except requests.RequestException as e:
            print(f"[TIKTOK_ORGANIC] Request error ({context}): {e}")
            if attempt < MAX_RETRIES:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(
                    f"[TIKTOK_ORGANIC] Retrying in {wait}s (attempt {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(wait)
                continue
            raise

        wait = _check_error(data, context)
        if wait:
            if attempt < MAX_RETRIES:
                w = min(wait, BACKOFF_MAX)
                print(f"[TIKTOK_ORGANIC] Rate limited ({context}). Retrying in {w}s...")
                time.sleep(w)
                continue
            raise Exception(
                f"[TIKTOK_ORGANIC] Rate limit exceeded after {MAX_RETRIES} retries ({context})."
            )
        return data

    raise Exception(f"[TIKTOK_ORGANIC] Max retries exceeded ({context}).")


@dlt.resource(name="profile_stats", write_disposition="replace")
def get_profile_stats(
    open_id: str,
    access_token: str,
    capture: list[dict[str, Any]] | None = None,
):
    url = f"{TIKTOK_API_BASE}/v2/user/info/"
    params = {
        "fields": "follower_count,following_count,likes_count,video_count",
    }

    data = _do_request(
        "GET", url, access_token, params=params, context=f"open_id {open_id} profile"
    )

    user = data.get("data", {}).get("user", {})
    row = {
        "report_date": time.strftime("%Y-%m-%d"),
        "follower_count": int(user.get("follower_count", 0) or 0),
        "following_count": int(user.get("following_count", 0) or 0),
        "likes_count": int(user.get("likes_count", 0) or 0),
        "video_count": int(user.get("video_count", 0) or 0),
    }
    # SDD-D archive seam (D1): append the row pre-yield so the post-run
    # archiver can persist it before the live replace drops it (ARC-R2).
    if capture is not None:
        capture.append(row)
    yield row


@dlt.resource(name="videos_organic", write_disposition="replace")
def get_videos_organic(open_id: str, access_token: str):
    url = (
        f"{TIKTOK_API_BASE}/v2/video/list/"
        f"?fields=id,title,create_time,like_count,comment_count,share_count,view_count"
    )
    body: dict = {"max_count": 20, "cursor": 0}
    has_more = True

    while has_more:
        data = _do_request(
            "POST",
            url,
            access_token,
            json_body=body,
            context=f"open_id {open_id} videos",
        )

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
                "report_date": time.strftime("%Y-%m-%d"),
            }

        body["cursor"] = result.get("cursor", 0)
        has_more = result.get("has_more", False)


@dlt.source
def tiktok_organic_source(
    open_id: str,
    client_key: str,
    client_secret: str,
    refresh_token: str,
    capture: dict[str, list[dict[str, Any]]] | None = None,
):
    state = dlt.current.source_state()
    tokens = state.setdefault("tiktok_organic_tokens", {})

    if "access_token" not in tokens:
        tokens["access_token"] = None
        tokens["expires_at"] = 0

    now = time.time()
    if not tokens.get("access_token") or now > tokens.get("expires_at", 0) - 300:
        stored_rt = state.get("tiktok_organic_refresh_token", refresh_token)
        new_tokens = _refresh_access_token(client_key, client_secret, stored_rt)
        tokens["access_token"] = new_tokens["access_token"]
        tokens["expires_at"] = now + new_tokens["expires_in"]
        state["tiktok_organic_refresh_token"] = new_tokens.get("refresh_token", stored_rt)

    access_token = tokens["access_token"]

    # SDD-D (D1): ``capture`` fans out to get_profile_stats; default None keeps
    # the pre-archive behavior byte-identical.
    stats_capture = capture["profile_stats"] if capture is not None else None
    return [
        get_profile_stats(open_id, access_token, capture=stats_capture),
        get_videos_organic(open_id, access_token),
    ]


def main():
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="TikTok Organic dlt extractor")
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    args = parser.parse_args()

    client_file = client_config.client_file_path(args.client)

    if not os.path.exists(client_file):
        print(f"[TIKTOK_ORGANIC] Client file not found: {client_file}")
        exit(1)

    client = client_config.load_client(args.client)

    if not client.get("active", True):
        print(f"[TIKTOK_ORGANIC] Client {args.client} is not active. Skipping.")
        exit(0)

    errors = client_config.validate_client(client)
    if errors:
        for error in errors:
            print(f"[TIKTOK_ORGANIC] {error}")
        exit(1)

    connector = client["connectors"].get("tiktok_organic", {})
    if not connector.get("enabled"):
        print(
            "[TIKTOK_ORGANIC] TikTok Organic connector"
            f" not enabled for client {args.client}. Skipping."
        )
        exit(0)

    missing = client_config.missing_envs("tiktok_organic", connector)
    if missing:
        for env_name in missing:
            print(
                f"[TIKTOK_ORGANIC] Environment variable {env_name} is not set"
                f" for client {args.client}."
            )
        exit(1)

    open_id = connector["open_id"]
    client_key = os.environ[connector["client_key_env"]]
    client_secret = os.environ[connector["client_secret_env"]]
    refresh_token = os.environ[connector["refresh_token_env"]]

    print(f"[TIKTOK_ORGANIC] Extracting data for client '{args.client}' (open_id {open_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"tiktok_organic_{args.client}",
        destination="postgres",
        dataset_name=client_config.raw_dataset(args.client, "tiktok_organic"),
    )
    # SDD-D (D1): capture this run's profile_stats row in memory while dlt
    # extracts — zero extra API calls (ARC-R2/NFR-2).
    capture: dict[str, list[dict[str, Any]]] = {"profile_stats": []}
    info = pipeline.run(
        tiktok_organic_source(open_id, client_key, client_secret, refresh_token, capture=capture)
    )
    print(f"[TIKTOK_ORGANIC] Done: {info}")

    # ─── SDD-D post-run archive glue (D6): append-only _history table ───────
    # Fail-loud (ARC-S5): any archive error prints the marker and exits 1 so
    # the existing dlt_tiktok_organic audit step reports failed. Success keeps
    # the exit code 0.
    captured_at = datetime.now(timezone.utc)
    dataset = client_config.raw_dataset(args.client, "tiktok_organic")
    profile_rows = archiver.flatten_profile_stats(capture["profile_stats"], captured_at)
    try:
        with pipeline.sql_client() as client:
            client.execute_sql(
                archiver.create_table_ddl(
                    dataset,
                    archiver.PROFILE_STATS_HISTORY,
                    archiver.profile_stats_columns(),
                    archiver.PROFILE_NATURAL_KEYS,
                )
            )
            sql, params = archiver.insert_on_conflict(
                dataset,
                archiver.PROFILE_STATS_HISTORY,
                [name for name, _ in archiver.profile_stats_columns()],
                profile_rows,
                archiver.PROFILE_NATURAL_KEYS,
            )
            if sql is not None:
                client.execute_sql(sql, *params)
    except Exception as exc:
        print(f"[TIKTOK] Archive failed: {exc}")
        exit(1)


if __name__ == "__main__":
    main()
