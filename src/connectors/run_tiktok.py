import argparse
import os
import time

import dlt
from dlt.sources.helpers import requests

from agency_analytics import client_config

TIKTOK_API_BASE = "https://business-api.tiktok.com/open_api/v1.3"
MAX_RETRIES = 5


def _tiktok_request(url, max_retries=MAX_RETRIES, **kwargs):
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, **kwargs)
            data = response.json()
        except requests.RequestException as e:
            if attempt == max_retries:
                print(f"[TIKTOK] Max retries ({max_retries}) exceeded: {e}")
                raise
            wait = 2**attempt
            print(
                f"[TIKTOK] Request failed (attempt {attempt}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        if data.get("code") == 0:
            return data

        code = data.get("code")
        msg = data.get("message", "")

        if code == 40004:
            if attempt == max_retries:
                raise Exception(f"[TIKTOK] Max retries ({max_retries}) exceeded on rate limit")
            wait = 2**attempt
            print(f"[TIKTOK] Rate limited (attempt {attempt}/{max_retries}). Waiting {wait}s...")
            time.sleep(wait)
            continue

        if code == 40007:
            raise Exception("[TIKTOK] Token expired. Renew in .env and redeploy.")

        raise Exception(f"[TIKTOK] API error {code}: {msg}")

    raise Exception(f"[TIKTOK] Max retries ({max_retries}) exceeded without response")


@dlt.resource(name="ads", write_disposition="replace")
def get_ads(account_id: str, access_token: str):
    url = f"{TIKTOK_API_BASE}/ad/get/"
    params = {
        "advertiser_id": account_id,
        "fields": (
            '["ad_id","ad_name","ad_status","stat_cost","stat_impression",'
            '"stat_click","stat_ctr","stat_cpc","stat_cpm","stat_reach",'
            '"stat_datetime"]'
        ),
        "page_size": 100,
    }
    headers = {
        "Access-Token": access_token,
        "Content-Type": "application/json",
    }
    page = 1
    total_pages = 1

    while page <= total_pages:
        params["page"] = page
        data = _tiktok_request(url, params=params, headers=headers)
        body = data.get("data", {})
        total_pages = body.get("page_info", {}).get("total_page", 1)

        for ad in body.get("list", []):
            yield {
                "ad_id": ad.get("ad_id"),
                "ad_name": ad.get("ad_name"),
                "status": ad.get("ad_status"),
                "spend": float(ad.get("stat_cost", 0) or 0),
                "impressions": int(ad.get("stat_impression", 0) or 0),
                "clicks": int(ad.get("stat_click", 0) or 0),
                "ctr": float(ad.get("stat_ctr", 0) or 0),
                "cpc": float(ad.get("stat_cpc", 0) or 0),
                "cpm": float(ad.get("stat_cpm", 0) or 0),
                "reach": int(ad.get("stat_reach", 0) or 0),
                "date": ad.get("stat_datetime"),
            }

        page += 1


@dlt.resource(name="campaigns", write_disposition="replace")
def get_campaigns(account_id: str, access_token: str):
    url = f"{TIKTOK_API_BASE}/campaign/get/"
    params = {
        "advertiser_id": account_id,
        "fields": (
            '["campaign_id","campaign_name","campaign_status","objective",'
            '"budget","budget_mode","cost","impressions","clicks","reach",'
            '"start_time","end_time","create_time"]'
        ),
        "page_size": 100,
    }
    headers = {
        "Access-Token": access_token,
        "Content-Type": "application/json",
    }
    page = 1
    total_pages = 1

    while page <= total_pages:
        params["page"] = page
        data = _tiktok_request(url, params=params, headers=headers)
        body = data.get("data", {})
        total_pages = body.get("page_info", {}).get("total_page", 1)

        for c in body.get("list", []):
            budget_mode = c.get("budget_mode")
            if budget_mode == "DAILY":
                budget_type = "DAILY"
            elif budget_mode == "TOTAL":
                budget_type = "LIFETIME"
            else:
                budget_type = budget_mode

            yield {
                "campaign_id": c.get("campaign_id"),
                "campaign_name": c.get("campaign_name"),
                "status": c.get("campaign_status"),
                "objective": c.get("objective"),
                "budget": float(c["budget"]) if c.get("budget") else None,
                "budget_type": budget_type,
                "spend": float(c.get("cost", 0) or 0),
                "impressions": int(c.get("impressions", 0) or 0),
                "clicks": int(c.get("clicks", 0) or 0),
                "reach": int(c.get("reach", 0) or 0),
                "start_date": c.get("start_time", "")[:10] if c.get("start_time") else None,
                "end_date": c.get("end_time", "")[:10] if c.get("end_time") else None,
                "date": c.get("create_time", "")[:10] if c.get("create_time") else None,
            }

        page += 1


@dlt.source
def tiktok_ads_source(account_id: str, access_token: str):
    return [
        get_ads(account_id, access_token),
        get_campaigns(account_id, access_token),
    ]


def main():
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="TikTok Ads dlt extractor")
    parser.add_argument("--client", required=True, help="Client ID from clients/ YAML")
    args = parser.parse_args()

    client_file = client_config.client_file_path(args.client)

    if not os.path.exists(client_file):
        print(f"[TIKTOK] Client file not found: {client_file}")
        exit(1)

    client = client_config.load_client(args.client)

    if not client.get("active", True):
        print(f"[TIKTOK] Client {args.client} is not active. Skipping.")
        exit(0)

    errors = client_config.validate_client(client)
    if errors:
        for error in errors:
            print(f"[TIKTOK] {error}")
        exit(1)

    connector = client.get("connectors", {}).get("tiktok", {})
    if not connector.get("enabled"):
        print(f"[TIKTOK] TikTok Ads connector not enabled for client {args.client}. Skipping.")
        exit(0)

    missing = client_config.missing_envs("tiktok", connector)
    if missing:
        for env_name in missing:
            print(f"[TIKTOK] Environment variable {env_name} is not set for client {args.client}.")
        exit(1)

    account_id = connector["account_id"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[TIKTOK] Extracting data for client '{args.client}' (account {account_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"tiktok_{args.client}",
        destination="postgres",
        dataset_name=client_config.raw_dataset(args.client, "tiktok"),
    )
    info = pipeline.run(tiktok_ads_source(account_id, access_token))
    print(f"[TIKTOK] Done: {info}")


if __name__ == "__main__":
    main()
