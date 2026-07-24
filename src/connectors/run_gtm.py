import argparse
import json
import os
import time

import dlt
import yaml
from dlt.sources.helpers import requests

GTM_API_BASE = "https://tagmanager.googleapis.com/v2"


def _headers(access_token: str):
    return {"Authorization": f"Bearer {access_token}"}


def _handle_response(response, context: str):
    if response.status_code == 429:
        wait = int(response.headers.get("Retry-After", 30))
        print(f"[GTM] Rate limited ({context}). Waiting {wait}s...")
        time.sleep(wait)
        return None

    try:
        data = response.json()
    except requests.RequestException as e:
        print(f"[GTM] JSON parse error in {context}: {e}")
        raise

    if "error" in data:
        err = data["error"]
        code = err.get("code", 0)
        msg = err.get("message", "")
        if code == 429:
            wait = 30
            print(f"[GTM] Rate limited (code 429). Waiting {wait}s...")
            time.sleep(wait)
            return None
        elif code in (401, 403):
            raise Exception(
                f"[GTM] Token expired or access denied ({context}). "
                f"Renew the access token in .env and redeploy."
            )
        elif code == 404:
            raise Exception(f"[GTM] Resource not found ({context}): {msg}")
        elif code == 400:
            raise Exception(f"[GTM] Bad request ({context}): {msg}")
        else:
            raise Exception(f"[GTM] API error {code} ({context}): {msg}")
    return data


@dlt.resource(name="containers", write_disposition="replace")
def get_containers(account_path: str, access_token: str):
    url = f"{GTM_API_BASE}/{account_path}/containers"
    while True:
        try:
            response = requests.get(url, headers=_headers(access_token))
        except requests.RequestException as e:
            print(f"[GTM] Request error (containers): {e}")
            raise

        data = _handle_response(response, "containers")
        if data is None:
            continue

        for container in data.get("container", []):
            yield {
                "container_id": container.get("containerId"),
                "account_id": container.get("accountId"),
                "name": container.get("name"),
                "public_id": container.get("publicId"),
                "usage_context": json.dumps(container.get("usageContext", [])),
            }
        break


@dlt.resource(name="tags", write_disposition="replace")
def get_tags(account_path: str, access_token: str):
    containers_url = f"{GTM_API_BASE}/{account_path}/containers"
    container_ids = []

    while True:
        try:
            containers_resp = requests.get(containers_url, headers=_headers(access_token))
        except requests.RequestException as e:
            print(f"[GTM] Request error (containers list for tags): {e}")
            raise

        containers_data = _handle_response(containers_resp, "containers list for tags")
        if containers_data is None:
            continue

        for c in containers_data.get("container", []):
            container_ids.append(c["containerId"])
        break

    for cid in container_ids:
        tags_url = f"{GTM_API_BASE}/{account_path}/containers/{cid}/tags"
        while True:
            try:
                tags_resp = requests.get(tags_url, headers=_headers(access_token))
            except requests.RequestException as e:
                print(f"[GTM] Request error (tags container {cid}): {e}")
                raise

            tags_data = _handle_response(tags_resp, f"tags container {cid}")
            if tags_data is None:
                continue

            for tag in tags_data.get("tag", []):
                firing = tag.get("firingTriggerId", [])
                blocking = tag.get("blockingTriggerId", [])
                tag_manager_url = (
                    f"https://tagmanager.google.com/#/container/{account_path}"
                    f"/workspaces/{tag.get('workspaceId', '')}/tag/{tag.get('tagId', '')}"
                )
                yield {
                    "tag_id": tag.get("tagId"),
                    "container_id": cid,
                    "type": tag.get("type"),
                    "name": tag.get("name"),
                    "firing_triggers": json.dumps(firing),
                    "blocking_triggers": json.dumps(blocking),
                    "tag_manager_url": tag_manager_url,
                }
            break


@dlt.resource(name="triggers", write_disposition="replace")
def get_triggers(account_path: str, access_token: str):
    containers_url = f"{GTM_API_BASE}/{account_path}/containers"
    container_ids = []

    while True:
        try:
            containers_resp = requests.get(containers_url, headers=_headers(access_token))
        except requests.RequestException as e:
            print(f"[GTM] Request error (containers list for triggers): {e}")
            raise

        containers_data = _handle_response(containers_resp, "containers list for triggers")
        if containers_data is None:
            continue

        for c in containers_data.get("container", []):
            container_ids.append(c["containerId"])
        break

    for cid in container_ids:
        triggers_url = f"{GTM_API_BASE}/{account_path}/containers/{cid}/triggers"
        while True:
            try:
                trig_resp = requests.get(triggers_url, headers=_headers(access_token))
            except requests.RequestException as e:
                print(f"[GTM] Request error (triggers container {cid}): {e}")
                raise

            trig_data = _handle_response(trig_resp, f"triggers container {cid}")
            if trig_data is None:
                continue

            for trigger in trig_data.get("trigger", []):
                yield {
                    "trigger_id": trigger.get("triggerId"),
                    "container_id": cid,
                    "type": trigger.get("type"),
                    "name": trigger.get("name"),
                    "filter_json": json.dumps(trigger.get("filter", [])),
                }
            break


@dlt.source
def gtm_source(account_path: str, access_token: str):
    return [
        get_containers(account_path, access_token),
        get_tags(account_path, access_token),
        get_triggers(account_path, access_token),
    ]


if __name__ == "__main__":
    from dotenv import load_dotenv

    try:
        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Google Tag Manager API v2 dlt extractor")
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
        print(f"[GTM] Client file not found: {client_file}")
        exit(1)

    with open(client_file) as f:
        client = yaml.safe_load(f)

    if not client.get("active", True):
        print(f"[GTM] Client {args.client} is not active. Skipping.")
        exit(0)

    connector = client["connectors"].get("gtm", {})
    if not connector.get("enabled"):
        print(f"[GTM] GTM connector not enabled for client {args.client}. Skipping.")
        exit(0)

    account_path = connector["account_path"]
    token_env = connector["token_env"]
    access_token = os.environ[token_env]

    print(f"[GTM] Extracting data for client '{args.client}' (path {account_path})...")

    pipeline = dlt.pipeline(
        pipeline_name=f"gtm_{args.client}",
        destination="postgres",
        dataset_name="raw_gtm",
    )
    info = pipeline.run(gtm_source(account_path, access_token))
    print(f"[GTM] Done: {info}")
