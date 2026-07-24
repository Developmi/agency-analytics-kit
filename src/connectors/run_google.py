import argparse
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

GOOGLE_API_BASE = "https://googleads.googleapis.com/v25"
MAX_RETRIES = 5


def _google_request(url, payload, headers, max_retries=MAX_RETRIES):
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(url, json=payload, headers=headers)
            data = response.json()
        except requests.RequestException as e:
            if attempt == max_retries:
                print(f"[GOOGLE] Max retries ({max_retries}) exceeded: {e}")
                raise
            wait = 2**attempt
            print(
                f"[GOOGLE] Request failed (attempt {attempt}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        if "error" not in data:
            return data

        error = data["error"]
        code = error.get("code", 0)
        msg = error.get("message", "")

        if code == 429:
            if attempt == max_retries:
                raise Exception(f"[GOOGLE] Max retries ({max_retries}) exceeded on rate limit")
            details = error.get("details", [{}])[0]
            wait_str = details.get("retryDelay", "30s")
            wait = int(wait_str.replace("s", "")) if isinstance(wait_str, str) else 30
            print(f"[GOOGLE] Rate limited (attempt {attempt}/{max_retries}). Waiting {wait}s...")
            time.sleep(wait)
            continue

        if code == 401:
            raise Exception("[GOOGLE] Token expired. Renew OAuth token and redeploy.")

        if code == 403:
            details = (
                error.get("details", [{}])[0].get("errors", [{}])[0] if error.get("details") else {}
            )
            reason = details.get("reason", "")
            if "developer-token" in reason.lower():
                raise Exception("[GOOGLE] Invalid developer token. Check .env.")
            raise Exception(f"[GOOGLE] Access denied: {msg}")

        raise Exception(f"[GOOGLE] API error {code}: {msg}")

    raise Exception(f"[GOOGLE] Max retries ({max_retries}) exceeded without response")


def _google_search(
    customer_id: str,
    developer_token: str,
    access_token: str,
    query: str,
    page_size: int = 10000,
):
    url = f"{GOOGLE_API_BASE}/customers/{customer_id}/googleAds:search"
    payload = {"query": query, "pageSize": page_size}
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": developer_token,
        "Content-Type": "application/json",
    }

    while True:
        data = _google_request(url, payload, headers)
        for row in data.get("results", []):
            yield row

        next_page = data.get("nextPageToken")
        if not next_page:
            break
        payload["pageToken"] = next_page


@dlt.resource(name="ads", write_disposition="replace")
def get_ads(customer_id: str, developer_token: str, access_token: str):
    query = """
        SELECT
            ad_group_ad.ad.id,
            ad_group_ad.ad.name,
            ad_group_ad.status,
            ad_group.id,
            campaign.id,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions,
            metrics.cost_per_conversion,
            segments.date
        FROM ad_group_ad
        WHERE segments.date DURING LAST_30_DAYS
        ORDER BY segments.date DESC
    """

    for row in _google_search(customer_id, developer_token, access_token, query):
        ad = row.get("adGroupAd", {}).get("ad", {})
        ad_group = row.get("adGroup", {})
        campaign = row.get("campaign", {})
        metrics = row.get("metrics", {})
        segments = row.get("segments", {})
        ad_name = ad.get("name")
        if isinstance(ad_name, dict):
            ad_name = ad_name.get("value")

        yield {
            "ad_id": ad.get("id"),
            "ad_group_id": ad_group.get("id"),
            "campaign_id": campaign.get("id"),
            "ad_name": ad_name,
            "status": row.get("adGroupAd", {}).get("status"),
            "spend_usd": float(metrics.get("costMicros", 0) or 0) / 1_000_000,
            "impressions": int(metrics.get("impressions", 0) or 0),
            "clicks": int(metrics.get("clicks", 0) or 0),
            "ctr": float(metrics.get("ctr", 0) or 0),
            "average_cpc": (
                float(metrics["averageCpc"]) / 1_000_000 if metrics.get("averageCpc") else 0.0
            ),
            "conversions": float(metrics.get("conversions", 0) or 0),
            "cost_per_conversion": (
                float(metrics["costPerConversion"]) / 1_000_000
                if metrics.get("costPerConversion")
                else 0.0
            ),
            "date": segments.get("date"),
        }


@dlt.resource(name="campaigns", write_disposition="replace")
def get_campaigns(customer_id: str, developer_token: str, access_token: str):
    query = """
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.advertising_channel_type,
            campaign.budget.amount_micros,
            campaign.budget.type,
            campaign.start_date,
            campaign.end_date,
            metrics.cost_micros,
            metrics.impressions,
            metrics.clicks,
            metrics.ctr,
            metrics.average_cpc,
            metrics.conversions,
            metrics.cost_per_conversion
        FROM campaign
        WHERE campaign.status != 'REMOVED'
        ORDER BY campaign.id
    """

    for row in _google_search(customer_id, developer_token, access_token, query):
        campaign = row.get("campaign", {})
        metrics = row.get("metrics", {})
        budget = campaign.get("budget", {})
        budget_amount = budget.get("amountMicros")

        yield {
            "campaign_id": campaign.get("id"),
            "campaign_name": campaign.get("name"),
            "status": campaign.get("status"),
            "advertising_channel_type": campaign.get("advertisingChannelType"),
            "budget": float(budget_amount) / 1_000_000 if budget_amount else None,
            "budget_type": budget.get("type"),
            "spend_usd": float(metrics.get("costMicros", 0) or 0) / 1_000_000,
            "impressions": int(metrics.get("impressions", 0) or 0),
            "clicks": int(metrics.get("clicks", 0) or 0),
            "ctr": float(metrics.get("ctr", 0) or 0),
            "average_cpc": (
                float(metrics["averageCpc"]) / 1_000_000 if metrics.get("averageCpc") else 0.0
            ),
            "conversions": float(metrics.get("conversions", 0) or 0),
            "cost_per_conversion": (
                float(metrics["costPerConversion"]) / 1_000_000
                if metrics.get("costPerConversion")
                else 0.0
            ),
            "start_date": campaign.get("startDate"),
            "end_date": campaign.get("endDate"),
            "date": campaign.get("startDate"),
        }


@dlt.source
def google_ads_source(customer_id: str, developer_token: str, access_token: str):
    return [
        get_ads(customer_id, developer_token, access_token),
        get_campaigns(customer_id, developer_token, access_token),
    ]


def main():
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Google Ads dlt extractor")
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
        print(f"[GOOGLE] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[GOOGLE] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client.get("connectors", {}).get("google", {})
    if not connector.get("enabled"):
        print(f"[GOOGLE] Google Ads connector not enabled for client {args.client}. Skipping.")
        exit(0)

    customer_id = connector["customer_id"]
    token_env = connector["token_env"]
    developer_token = os.environ["GOOGLE_ADS_DEVELOPER_TOKEN"]
    access_token = os.environ[token_env]

    print(f"[GOOGLE] Extracting data for client '{args.client}' (customer {customer_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"google_{args.client}",
        destination="postgres",
        dataset_name="raw_google",
    )
    info = pipeline.run(google_ads_source(customer_id, developer_token, access_token))
    print(f"[GOOGLE] Done: {info}")


if __name__ == "__main__":
    main()
