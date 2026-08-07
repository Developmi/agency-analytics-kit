import argparse
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

TIKTOK_API_BASE = "https://open.tiktokapis.com"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120


def _refresh_access_token(
    client_key: str, client_secret: str, refresh_token: str
) -> dict:
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
                    f"[TIKTOK_ORGANIC] Retrying in {wait}s"
                    f" (attempt {attempt + 1}/{MAX_RETRIES})..."
                )
                time.sleep(wait)
                continue
            raise

        wait = _check_error(data, context)
        if wait:
            if attempt < MAX_RETRIES:
                w = min(wait, BACKOFF_MAX)
                print(
                    f"[TIKTOK_ORGANIC] Rate limited ({context}). Retrying in {w}s..."
                )
                time.sleep(w)
                continue
            raise Exception(
                f"[TIKTOK_ORGANIC] Rate limit exceeded"
                f" after {MAX_RETRIES} retries ({context})."
            )
        return data

    raise Exception(f"[TIKTOK_ORGANIC] Max retries exceeded ({context}).")


@dlt.resource(name="profile_stats", write_disposition="replace")
def get_profile_stats(open_id: str, access_token: str):
    url = f"{TIKTOK_API_BASE}/v2/user/info/"
    params = {
        "fields": "follower_count,following_count,likes_count,video_count",
    }

    data = _do_request(
        "GET", url, access_token, params=params, context=f"open_id {open_id} profile"
    )

    user = data.get("data", {}).get("user", {})
    yield {
        "report_date": time.strftime("%Y-%m-%d"),
        "follower_count": int(user.get("follower_count", 0) or 0),
        "following_count": int(user.get("following_count", 0) or 0),
        "likes_count": int(user.get("likes_count", 0) or 0),
        "video_count": int(user.get("video_count", 0) or 0),
    }


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
        state["tiktok_organic_refresh_token"] = new_tokens.get(
            "refresh_token", stored_rt
        )

    access_token = tokens["access_token"]

    return [
        get_profile_stats(open_id, access_token),
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

    clients_dir = os.environ.get("CLIENTS_DIR")
    if not clients_dir:
        clients_dir = "/app/clients"
        if not os.path.exists(clients_dir):
            clients_dir = os.path.normpath(
                os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "..", "..", "clients"
                )
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
    client_key = os.environ[connector["client_key_env"]]
    client_secret = os.environ[connector["client_secret_env"]]
    refresh_token = os.environ[connector["refresh_token_env"]]

    print(
        f"[TIKTOK_ORGANIC] Extracting data for client"
        f" '{args.client}' (open_id {open_id})..."
    )

    pipeline = dlt.pipeline(
        pipeline_name=f"tiktok_organic_{args.client}",
        destination="postgres",
        dataset_name="raw_tiktok_organic",
    )
    info = pipeline.run(
        tiktok_organic_source(open_id, client_key, client_secret, refresh_token)
    )
    print(f"[TIKTOK_ORGANIC] Done: {info}")


if __name__ == "__main__":
    main()
