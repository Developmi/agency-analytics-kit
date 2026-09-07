"""Shared fixtures and configuration for all dlt connector tests."""

import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

# Add dlt_scripts directory to path so all tests can import modules directly
SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "02-pipeline",
    "dlt_scripts",
)
sys.path.insert(0, SCRIPTS_DIR)

GA4_SERVICE_ACCOUNT_JSON = {
    "client_email": "test@example.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\n",
}

# ─── Fixtures ────────────────────────────────────────────────────────────


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
