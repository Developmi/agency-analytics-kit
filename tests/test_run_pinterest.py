import os

import pytest
import yaml

MODULE = "run_pinterest"


def _mock_response(status=200, json_data=None):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.status_code = status
    m.json.return_value = json_data or {}
    m.headers = {}
    return m


def test_boards_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "description": "My pins",
                "pin_count": 50,
                "follower_count": 10,
                "created_at": "2024-01-01T00:00:00Z",
            }
        ],
        "bookmark": None,
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get",
        lambda url, headers=None, params=None: _mock_response(200, boards_response),
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["boards"])

    assert len(items) == 1
    assert items[0]["board_id"] == "board_1"
    assert items[0]["name"] == "My Board"
    assert items[0]["description"] == "My pins"
    assert items[0]["pin_count"] == 50
    assert items[0]["follower_count"] == 10
    assert items[0]["created_at"] == "2024-01-01T00:00:00Z"


def test_pins_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "pin_count": 1,
                "follower_count": 0,
                "created_at": "",
            }
        ],
        "bookmark": None,
    }
    pins_response = {
        "items": [
            {
                "id": "pin_1",
                "title": "My Pin",
                "description": "A cool pin",
                "link": "https://example.com",
                "destination_url": "https://example.com/dest",
                "pin_count": 5,
                "save_count": 3,
                "created_at": "2024-01-02T00:00:00Z",
            }
        ],
        "bookmark": None,
    }

    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(200, boards_response)
        return _mock_response(200, pins_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["pins"])

    assert len(items) == 1
    assert items[0]["pin_id"] == "pin_1"
    assert items[0]["board_id"] == "board_1"
    assert items[0]["title"] == "My Pin"
    assert items[0]["destination_url"] == "https://example.com/dest"
    assert items[0]["pin_count"] == 5
    assert items[0]["save_count"] == 3
    assert items[0]["created_at"] == "2024-01-02T00:00:00Z"


def test_board_insights_success(monkeypatch):
    boards_response = {
        "items": [
            {
                "id": "board_1",
                "name": "My Board",
                "pin_count": 1,
                "follower_count": 0,
                "created_at": "",
            }
        ],
        "bookmark": None,
    }
    insights_response = {
        "date": "2024-01-03",
        "metrics": {
            "IMPRESSION": {"reach": 500, "count": 1000},
            "SAVE": {"count": 50},
            "CLICK": {"count": 25},
        },
    }

    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(200, boards_response)
        return _mock_response(200, insights_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["board_insights"])

    assert len(items) == 1
    assert items[0]["board_id"] == "board_1"
    assert items[0]["report_date"] == "2024-01-03"
    assert items[0]["reach"] == 500
    assert items[0]["impressions"] == 1000
    assert items[0]["saves"] == 50
    assert items[0]["clicks"] == 25


def test_rate_limit(monkeypatch):
    call_count = [0]

    def mock_get(url, headers=None, params=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return _mock_response(429, {})
        return _mock_response(
            200,
            {
                "items": [
                    {
                        "id": "board_1",
                        "name": "B",
                        "pin_count": 0,
                        "follower_count": 0,
                        "created_at": "",
                    }
                ],
                "bookmark": None,
            },
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    items = list(source.resources["boards"])

    assert len(items) == 1
    assert call_count[0] == 2


def test_token_expired(monkeypatch):
    monkeypatch.setattr(
        f"{MODULE}.requests.get", lambda url, headers=None, params=None: _mock_response(401, {})
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token")
    with pytest.raises(Exception, match="Token expired"):
        list(source.resources["boards"])


def test_pins_with_board_id(monkeypatch):
    pins_response = {
        "items": [
            {
                "id": "pin_1",
                "title": "Pin",
                "pin_count": 1,
                "save_count": 0,
            }
        ],
        "bookmark": None,
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get",
        lambda url, headers=None, params=None: _mock_response(200, pins_response),
    )

    from run_pinterest import pinterest_source

    source = pinterest_source("mock_token", board_id="specific_board")
    items = list(source.resources["pins"])

    assert len(items) == 1
    assert items[0]["pin_id"] == "pin_1"
    assert items[0]["board_id"] == "specific_board"


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    pi_cfg = template["connectors"]["pinterest"]
    assert "board_id" in pi_cfg
    assert "token_env" in pi_cfg
