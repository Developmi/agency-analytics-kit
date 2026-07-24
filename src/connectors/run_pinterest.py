from __future__ import annotations

import argparse
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

PINTEREST_API_BASE = "https://api.pinterest.com/v5"

MAX_RETRIES = 5
BACKOFF_BASE = 2
BACKOFF_MAX = 120


def _headers(access_token: str):
    return {"Authorization": f"Bearer {access_token}"}


def _do_request(url, headers, params, context, retries=MAX_RETRIES):
    for attempt in range(retries + 1):
        try:
            response = requests.get(url, headers=headers, params=params)
        except requests.RequestException as e:
            print(f"[PINTEREST] Request error ({context}): {e}")
            if attempt < retries:
                wait = min(BACKOFF_BASE * (2**attempt), BACKOFF_MAX)
                print(f"[PINTEREST] Retrying in {wait}s (attempt {attempt + 1}/{retries})...")
                time.sleep(wait)
                continue
            raise

        if response.status_code == 429:
            if attempt < retries:
                wait = min(int(response.headers.get("Retry-After", 30)), BACKOFF_MAX)
                print(f"[PINTEREST] Rate limited ({context}). Retrying in {wait}s...")
                time.sleep(wait)
                continue
            raise Exception(f"[PINTEREST] Rate limit exceeded after {retries} retries ({context}).")

        if response.status_code == 401:
            raise Exception(
                f"[PINTEREST] Token expired ({context}). "
                f"Renew the access token in .env and redeploy."
            )

        if response.status_code != 200:
            try:
                err = response.json()
                code = err.get("code", 0)
                msg = err.get("message", "")
            except Exception:
                code = response.status_code
                msg = response.reason or ""
            raise Exception(f"[PINTEREST] API error {code} ({context}): {msg}")

        try:
            data = response.json()
        except Exception as e:
            print(f"[PINTEREST] JSON parse error ({context}): {e}")
            raise

        return data

    raise Exception(f"[PINTEREST] Max retries exceeded ({context}).")


@dlt.resource(name="boards", write_disposition="replace")
def get_boards(access_token: str):
    headers = _headers(access_token)
    url: str | None = f"{PINTEREST_API_BASE}/boards"
    params = {"page_size": 100}

    while url:
        data = _do_request(url, headers, params, "boards")
        for item in data.get("items", []):
            yield {
                "board_id": item.get("id"),
                "name": item.get("name"),
                "description": item.get("description"),
                "pin_count": int(item.get("pin_count", 0) or 0),
                "follower_count": int(item.get("follower_count", 0) or 0),
                "created_at": item.get("created_at"),
            }

        bookmark = data.get("bookmark")
        url = f"{PINTEREST_API_BASE}/boards" if bookmark else None
        params = {"page_size": 100, "bookmark": bookmark} if bookmark else {"page_size": 100}


@dlt.resource(name="pins", write_disposition="replace")
def get_pins(access_token: str, board_id: str | None = None):
    headers = _headers(access_token)

    if board_id:
        board_ids = [board_id]
    else:
        board_ids = _list_board_ids(headers)

    for bid in board_ids:
        pins_url: str | None = f"{PINTEREST_API_BASE}/boards/{bid}/pins"
        pins_params = {"page_size": 100}
        while pins_url:
            data = _do_request(pins_url, headers, pins_params, f"pins for board {bid}")
            for pin in data.get("items", []):
                yield {
                    "pin_id": pin.get("id"),
                    "board_id": bid,
                    "title": pin.get("title"),
                    "description": pin.get("description"),
                    "link": pin.get("link"),
                    "destination_url": pin.get("destination_url"),
                    "pin_count": int(pin.get("pin_count", 0) or 0),
                    "save_count": int(pin.get("save_count", 0) or 0),
                    "created_at": pin.get("created_at"),
                }

            bookmark = data.get("bookmark")
            pins_url = f"{PINTEREST_API_BASE}/boards/{bid}/pins" if bookmark else None
            pins_params = (
                {"page_size": 100, "bookmark": bookmark} if bookmark else {"page_size": 100}
            )


@dlt.resource(name="board_insights", write_disposition="replace")
def get_board_insights(access_token: str, board_id: str | None = None):
    headers = _headers(access_token)

    if board_id:
        board_ids = [board_id]
    else:
        board_ids = _list_board_ids(headers)

    for bid in board_ids:
        insights_url = f"{PINTEREST_API_BASE}/boards/{bid}/insights"
        insights_params = {
            "metric_types": "IMPRESSION,SAVE,CLICK",
            "date": "today",
        }
        data = _do_request(insights_url, headers, insights_params, f"insights board {bid}")
        metrics = data.get("metrics", {})
        report_date = data.get("date", time.strftime("%Y-%m-%d"))
        yield {
            "report_date": report_date,
            "board_id": bid,
            "reach": metrics.get("IMPRESSION", {}).get("reach", 0) or 0,
            "impressions": metrics.get("IMPRESSION", {}).get("count", 0) or 0,
            "saves": metrics.get("SAVE", {}).get("count", 0) or 0,
            "clicks": metrics.get("CLICK", {}).get("count", 0) or 0,
        }


def _list_board_ids(headers):
    board_ids = []
    url = f"{PINTEREST_API_BASE}/boards"
    params = {"page_size": 100}
    while url:
        data = _do_request(url, headers, params, "boards list")
        for item in data.get("items", []):
            board_ids.append(item["id"])
        bookmark = data.get("bookmark")
        url = f"{PINTEREST_API_BASE}/boards" if bookmark else None
        params = {"page_size": 100, "bookmark": bookmark} if bookmark else {"page_size": 100}
    return board_ids


@dlt.source
def pinterest_source(access_token: str, board_id: str | None = None):
    return [
        get_boards(access_token),
        get_pins(access_token, board_id),
        get_board_insights(access_token, board_id),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Pinterest API v5 dlt extractor")
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
        print(f"[PINTEREST] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[PINTEREST] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("pinterest", {})
    if not connector.get("enabled"):
        print(f"[PINTEREST] Pinterest connector not enabled for client {args.client}. Skipping.")
        exit(0)

    board_id = connector.get("board_id") or None
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[PINTEREST] Extracting data for client '{args.client}'...")

    pipeline = dlt.pipeline(
        pipeline_name=f"pinterest_{args.client}",
        destination="postgres",
        dataset_name="raw_pinterest",
    )
    info = pipeline.run(pinterest_source(access_token, board_id))
    print(f"[PINTEREST] Done: {info}")
