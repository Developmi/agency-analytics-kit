from __future__ import annotations

import argparse
import os
import time
from typing import Any

import dlt
import yaml
from dlt.sources.helpers import requests

META_API_BASE = "https://graph.facebook.com/v25.0"
MAX_RETRIES = 5


def _meta_request(url, max_retries=MAX_RETRIES, **kwargs):
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.get(url, **kwargs)
            data = response.json()
        except requests.HTTPError as e:
            # HTTP-level error (4xx/5xx) - parse the body for Meta's real error
            status = e.response.status_code
            try:
                error_data = e.response.json()
                error_code = error_data.get("error", {}).get("code", 0)
                error_msg = error_data.get("error", {}).get("message", str(e))
            except Exception:
                error_code = 0
                error_msg = str(e)

            print(f"[META] HTTP {status}: {error_msg}")

            # Rate limits - retry
            if status == 429 or error_code in (4, 17, 80000):
                if attempt == max_retries:
                    raise Exception(f"[META] Max retries ({max_retries}) exceeded on rate limit")
                wait = 2**attempt
                print(
                    f"[META] Rate limited (attempt {attempt}/{max_retries}). Retrying in {wait}s..."
                )
                time.sleep(wait)
                continue

            # Token expired - abort immediately
            if error_code == 190:
                raise Exception("[META] Token expired. Renew in .env and redeploy.")

            # Everything else is a hard error (400, 403, 500...) - abort immediately
            raise Exception(f"[META] API error: {error_msg}")

        except requests.RequestException as e:
            # Connection errors, timeouts, DNS failures - retry
            if attempt == max_retries:
                print(f"[META] Max retries ({max_retries}) exceeded: {e}")
                raise
            wait = 2**attempt
            print(
                f"[META] Request failed (attempt {attempt}/{max_retries}). Retrying in {wait}s..."
            )
            time.sleep(wait)
            continue

        if "error" not in data:
            return data

        # Meta returned error in JSON body (no HTTP error)
        error = data["error"]
        code = error.get("code", 0)
        msg = error.get("message", "")

        if code in (4, 17, 80000):
            if attempt == max_retries:
                raise Exception(f"[META] Max retries ({max_retries}) exceeded on rate limit")
            wait = (
                error.get("error_user_title", 30)
                if code == 80000
                else error.get("error_subcode", 30)
            )
            if isinstance(wait, str) or not isinstance(wait, (int, float)):
                wait = 60
            print(
                f"[META] Rate limited (code {code},"
                f" attempt {attempt}/{max_retries}). Waiting {wait}s..."
            )
            time.sleep(wait)
            continue

        if code == 190:
            raise Exception("[META] Token expired. Renew in .env and redeploy.")

        if code == 100:
            raise Exception(f"[META] Invalid parameter: {msg}")

        raise Exception(f"[META] API error {code}: {msg}")

    raise Exception(f"[META] Max retries ({max_retries}) exceeded without response")


def _get_insight(ad, field, default=0, cast_fn=float):
    """Extract a metric from nested Meta insights data."""
    insights = ad.get("insights")
    if isinstance(insights, dict):
        data = insights.get("data", [])
        if data:
            val = data[0].get(field)
            if val is not None:
                return cast_fn(val)
    return cast_fn(default)


@dlt.resource(
    name="ads",
    write_disposition="replace",
    columns={"date": {"data_type": "date"}},
)
def get_ads(account_id: str, access_token: str):
    url = f"{META_API_BASE}/act_{account_id}/ads"
    params: dict[str, Any] | None = {
        "fields": ",".join(
            [
                "id",
                "name",
                "status",
                "date_start",
                "insights.date_preset(yesterday){impressions,clicks,spend,reach,frequency,cpm,cpc}",
            ]
        ),
        "access_token": access_token,
        "limit": 100,
    }
    while url:
        data = _meta_request(url, params=params)
        for ad in data.get("data", []):
            yield {
                "ad_id": ad.get("id"),
                "ad_name": ad.get("name"),
                "status": ad.get("status"),
                "spend": _get_insight(ad, "spend"),
                "impressions": _get_insight(ad, "impressions", cast_fn=int),
                "clicks": _get_insight(ad, "clicks", cast_fn=int),
                "reach": _get_insight(ad, "reach", cast_fn=int),
                "frequency": _get_insight(ad, "frequency"),
                "cpm": _get_insight(ad, "cpm"),
                "cpc": _get_insight(ad, "cpc"),
                "date": _get_insight(ad, "date_start", default=None, cast_fn=str),
            }
        url = data.get("paging", {}).get("next")
        params = None


@dlt.resource(
    name="campaigns",
    write_disposition="replace",
    columns={"date": {"data_type": "date"}},
)
def get_campaigns(account_id: str, access_token: str):
    url = f"{META_API_BASE}/act_{account_id}/campaigns"
    params: dict[str, Any] | None = {
        "fields": ",".join(
            [
                "id",
                "name",
                "status",
                "objective",
                "daily_budget",
                "lifetime_budget",
                "start_time",
                "stop_time",
                "insights.date_preset(yesterday){impressions,clicks,spend,reach,frequency,cpm,cpc}",
            ]
        ),
        "access_token": access_token,
        "limit": 100,
    }
    while url:
        data = _meta_request(url, params=params)
        for c in data.get("data", []):
            daily = c.get("daily_budget")
            lifetime = c.get("lifetime_budget")
            if daily:
                budget = float(daily) / 100
                budget_type = "DAILY"
            elif lifetime:
                budget = float(lifetime) / 100
                budget_type = "LIFETIME"
            else:
                budget = None
                budget_type = None

            yield {
                "campaign_id": c.get("id"),
                "campaign_name": c.get("name"),
                "status": c.get("status"),
                "objective": c.get("objective"),
                "spend": _get_insight(c, "spend"),
                "impressions": _get_insight(c, "impressions", cast_fn=int),
                "clicks": _get_insight(c, "clicks", cast_fn=int),
                "reach": _get_insight(c, "reach", cast_fn=int),
                "frequency": _get_insight(c, "frequency"),
                "cpm": _get_insight(c, "cpm"),
                "cpc": _get_insight(c, "cpc"),
                "budget": budget,
                "budget_type": budget_type,
                "start_date": c.get("start_time", "")[:10] if c.get("start_time") else None,
                "end_date": c.get("stop_time", "")[:10] if c.get("stop_time") else None,
                "date": _get_insight(c, "date_start", default=None, cast_fn=str),
            }
        url = data.get("paging", {}).get("next")
        params = None


@dlt.source
def meta_ads_source(account_id: str, access_token: str):
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

    parser = argparse.ArgumentParser(description="Meta Ads dlt extractor")
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
        print(f"[META] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[META] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client.get("connectors", {}).get("meta", {})
    if not connector.get("enabled"):
        print(f"[META] Meta Ads connector not enabled for client {args.client}. Skipping.")
        exit(0)

    account_id = connector["account_id"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[META] Extracting data for client '{args.client}' (account {account_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"meta_{args.client}",
        destination="postgres",
        dataset_name="raw_meta",
    )
    info = pipeline.run(meta_ads_source(account_id, access_token))
    print(f"[META] Done: {info}")


if __name__ == "__main__":
    main()
