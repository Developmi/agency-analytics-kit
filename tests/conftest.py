"""Shared fixtures and configuration for all dlt connector tests."""

import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest
import yaml

# Add dlt_scripts directory to path so all tests can import modules directly
SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "02-pipeline",
    "dlt_scripts",
)
sys.path.insert(0, SCRIPTS_DIR)

# ─── Sample client data (ads) ────────────────────────────────────────────

SAMPLE_CLIENT_CONTENT = """
client_id: test_client
client_name: "Test Client"
schema: test_client
active: true

connectors:
  meta:
    enabled: true
    account_id: "123456789"
    token_env: META_TOKEN
  tiktok:
    enabled: true
    account_id: "987654321"
    token_env: TIKTOK_TOKEN
  google:
    enabled: true
    customer_id: "123-456-7890"
    token_env: GOOGLE_ADS_TOKEN
"""

# ─── Sample client data (organic + analytics) ────────────────────────────

SAMPLE_CLIENT_DATA = {
    "client_id": "test_client",
    "client_name": "Test Client",
    "schema": "client_test",
    "active": True,
    "connectors": {
        "youtube": {
            "enabled": True,
            "channel_id": "UC_test_channel",
            "token_env": "YOUTUBE_API_KEY_TEST",
        },
        "ga4": {
            "enabled": True,
            "property_id": "properties/123456789",
            "service_account": (
                "ewogICJjbGllbnRfZW1haWwiOiAidGVzdEBleGFtcGxlLmNvbSIsCiAgInByaXZhdGVfa2V5IjogIi0tLS0"
                "tQkVHSU4gUFJJVkFURSBLRVktLS0tLVxuIn0="
            ),
        },
        "gtm": {
            "enabled": True,
            "account_path": "accounts/123456789",
            "token_env": "GTM_ACCESS_TOKEN_TEST",
        },
    },
}

GA4_SERVICE_ACCOUNT_JSON = {
    "client_email": "test@example.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\n",
}

# ─── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def sample_client_yml():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
    tmp.write(SAMPLE_CLIENT_CONTENT)
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def sample_client_data_yml():
    """YAML fixture for organic/analytics connectors."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False)
    yaml.dump(SAMPLE_CLIENT_DATA, tmp)
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def ga4_service_account_file():
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(GA4_SERVICE_ACCOUNT_JSON, tmp)
    tmp.close()
    yield tmp.name
    os.unlink(tmp.name)


@pytest.fixture
def mock_meta_api():
    patcher = patch("run_meta.requests.get")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_tiktok_api():
    patcher = patch("run_tiktok.requests.get")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_google_api():
    patcher = patch("run_google.requests.post")
    mock = patcher.start()
    yield mock
    patcher.stop()


@pytest.fixture
def mock_sleep():
    patcher = patch("time.sleep")
    mock = patcher.start()
    yield mock
    patcher.stop()
