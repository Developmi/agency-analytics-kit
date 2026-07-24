import argparse
import base64
import json
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

GA4_API_BASE = "https://analyticsdata.googleapis.com/v1beta"


def _get_access_token(client_email: str, private_key: str) -> str:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.service_account import Credentials
    except ImportError:
        raise ImportError(
            "google-auth is required for GA4. Install it with: pip install google-auth"
        )

    scopes = ["https://www.googleapis.com/auth/analytics.readonly"]
    creds = Credentials.from_service_account_info(
        {"client_email": client_email, "private_key": private_key},
        scopes=scopes,
    )
    creds.refresh(Request())
    return creds.token


def _run_report(property_id: str, access_token: str, request_body: dict):
    url = f"{GA4_API_BASE}/properties/{property_id}:runReport"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=request_body)
    except requests.RequestException as e:
        print(f"[GA4] Request error: {e}")
        raise

    if response.status_code == 429:
        wait = int(response.headers.get("Retry-After", 30))
        print(f"[GA4] Rate limited. Waiting {wait}s...")
        time.sleep(wait)
        return None

    try:
        data = response.json()
    except requests.RequestException as e:
        print(f"[GA4] JSON parse error: {e}")
        raise

    if "error" in data:
        err = data["error"]
        code = err.get("code", 0)
        msg = err.get("message", "")
        if code == 429:
            wait = 30
            print(f"[GA4] Rate limited (code 429). Waiting {wait}s...")
            time.sleep(wait)
            return None
        elif code in (401, 403):
            raise Exception(
                f"[GA4] Token expired or access denied for property {property_id}. "
                f"Check service account permissions. {msg}"
            )
        elif code == 400:
            raise Exception(f"[GA4] Bad request: {msg}")
        else:
            raise Exception(f"[GA4] API error {code}: {msg}")
    return data


def _parse_rows(data):
    if not data or "rows" not in data:
        return []
    dim_headers = [h["name"] for h in data.get("dimensionHeaders", [])]
    met_headers = [h["name"] for h in data.get("metricHeaders", [])]
    rows = []
    for row in data["rows"]:
        dims = [v.get("value", "") for v in row.get("dimensionValues", [])]
        mets = [v.get("value", "") for v in row.get("metricValues", [])]
        record = {}
        for i, dh in enumerate(dim_headers):
            record[dh] = dims[i] if i < len(dims) else None
        for i, mh in enumerate(met_headers):
            record[mh] = mets[i] if i < len(mets) else None
        rows.append(record)
    return rows


def _resolve_service_account(service_account_raw: str) -> dict:
    try:
        sa_decoded = base64.b64decode(service_account_raw).decode("utf-8")
        return json.loads(sa_decoded)
    except (ValueError, json.JSONDecodeError):
        if not os.path.exists(service_account_raw):
            raise Exception(
                "[GA4] service_account is neither a valid"
                f" base64 JSON nor a file path: {service_account_raw}"
            )
        with open(service_account_raw) as f:
            return json.load(f)


@dlt.resource(name="daily_stats", write_disposition="replace")
def get_daily_stats(property_id: str, client_email: str, private_key: str):
    token = _get_access_token(client_email, private_key)
    request_body = {
        "dateRanges": [{"startDate": "30daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "date"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "newUsers"},
            {"name": "screenPageViews"},
            {"name": "bounceRate"},
            {"name": "averageSessionDuration"},
        ],
    }

    while True:
        data = _run_report(property_id, token, request_body)
        if data is None:
            token = _get_access_token(client_email, private_key)
            continue

        for row in _parse_rows(data):
            yield {
                "report_date": row.get("date"),
                "sessions": int(row.get("sessions", 0)),
                "total_users": int(row.get("totalUsers", 0)),
                "new_users": int(row.get("newUsers", 0)),
                "pageviews": int(row.get("screenPageViews", 0)),
                "bounce_rate": float(row.get("bounceRate", 0)),
                "avg_session_duration_seconds": float(row.get("averageSessionDuration", 0)),
            }
        break


@dlt.resource(name="page_analytics", write_disposition="replace")
def get_page_analytics(property_id: str, client_email: str, private_key: str):
    token = _get_access_token(client_email, private_key)
    request_body = {
        "dateRanges": [{"startDate": "30daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "date"}, {"name": "pagePath"}, {"name": "pageTitle"}],
        "metrics": [
            {"name": "screenPageViews"},
            {"name": "totalUsers"},
            {"name": "averageSessionDuration"},
            {"name": "bounceRate"},
        ],
    }

    while True:
        data = _run_report(property_id, token, request_body)
        if data is None:
            token = _get_access_token(client_email, private_key)
            continue

        for row in _parse_rows(data):
            yield {
                "report_date": row.get("date"),
                "page_path": row.get("pagePath"),
                "page_title": row.get("pageTitle"),
                "pageviews": int(row.get("screenPageViews", 0)),
                "unique_pageviews": int(row.get("totalUsers", 0)),
                "avg_time_on_page_seconds": float(row.get("averageSessionDuration", 0)),
                "bounce_rate": float(row.get("bounceRate", 0)),
            }
        break


@dlt.resource(name="event_analytics", write_disposition="replace")
def get_event_analytics(property_id: str, client_email: str, private_key: str):
    token = _get_access_token(client_email, private_key)
    request_body = {
        "dateRanges": [{"startDate": "30daysAgo", "endDate": "today"}],
        "dimensions": [{"name": "date"}, {"name": "eventName"}],
        "metrics": [
            {"name": "eventCount"},
            {"name": "totalUsers"},
        ],
    }

    while True:
        data = _run_report(property_id, token, request_body)
        if data is None:
            token = _get_access_token(client_email, private_key)
            continue

        for row in _parse_rows(data):
            yield {
                "report_date": row.get("date"),
                "event_name": row.get("eventName"),
                "event_count": int(row.get("eventCount", 0)),
                "user_count": int(row.get("totalUsers", 0)),
            }
        break


@dlt.source
def ga4_source(property_id: str, client_email: str, private_key: str):
    return [
        get_daily_stats(property_id, client_email, private_key),
        get_page_analytics(property_id, client_email, private_key),
        get_event_analytics(property_id, client_email, private_key),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Google Analytics 4 Data API dlt extractor")
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
        print(f"[GA4] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[GA4] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("ga4", {})
    if not connector.get("enabled"):
        print(f"[GA4] GA4 connector not enabled for client {args.client}. Skipping.")
        exit(0)

    property_id = connector["property_id"]
    service_account_raw = connector["service_account"]
    sa_json = _resolve_service_account(service_account_raw)

    client_email = sa_json["client_email"]
    private_key = sa_json["private_key"]

    print(f"[GA4] Extracting data for client '{args.client}' (property {property_id})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"ga4_{args.client}",
        destination="postgres",
        dataset_name="raw_ga4",
    )
    info = pipeline.run(ga4_source(property_id, client_email, private_key))
    print(f"[GA4] Done: {info}")
