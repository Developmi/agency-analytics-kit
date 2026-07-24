import os

import pytest
import yaml

MODULE = "run_instagram"


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def test_media_success(monkeypatch):
    media_response = {
        "data": [
            {
                "id": "media_1",
                "caption": "Nice pic",
                "media_type": "IMAGE",
                "like_count": 42,
                "comments_count": 7,
                "timestamp": "2024-01-01T12:00:00+0000",
                "permalink": "https://instagram.com/p/media_1",
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(media_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    assert items[0]["media_id"] == "media_1"
    assert items[0]["caption"] == "Nice pic"
    assert items[0]["media_type"] == "IMAGE"
    assert items[0]["like_count"] == 42
    assert items[0]["comments_count"] == 7
    assert items[0]["permalink"] == "https://instagram.com/p/media_1"
    assert items[0]["timestamp"] == "2024-01-01T12:00:00+0000"


def test_insights_success(monkeypatch):
    time_series_response = {
        "data": [
            {
                "name": "reach",
                "period": "day",
                "values": [{"value": 1000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "follower_count",
                "period": "day",
                "values": [{"value": 5000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }
    total_value_response = {
        "data": [
            {"name": "profile_views", "period": "day", "total_value": {"value": 150}},
            {"name": "views", "period": "day", "total_value": {"value": 2500}},
        ]
    }

    call_count = 0

    def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_json(time_series_response)
        return _mock_json(total_value_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_daily"])

    assert len(rows) == 1
    assert rows[0]["report_date"] == "2024-01-01"
    assert rows[0]["reach"] == 1000
    assert rows[0]["views"] == 2500
    assert rows[0]["profile_views"] == 150
    assert rows[0]["follower_count"] == 5000


def test_rate_limit(monkeypatch):
    calls = []

    def mock_get(url, params=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 4, "message": "Rate limit"}})
        return _mock_json(
            {
                "data": [
                    {
                        "id": "media_1",
                        "media_type": "IMAGE",
                        "like_count": 0,
                        "comments_count": 0,
                    }
                ],
                "paging": {"next": None},
            }
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 190, "message": "Token expired"}}
    monkeypatch.setattr(f"{MODULE}.requests.get", lambda url, params=None: _mock_json(error_data))

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token")
    with pytest.raises(Exception, match="Token expired"):
        list(source.resources["media"])


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    ig_cfg = template["connectors"]["instagram"]
    assert "instagram_business_id" in ig_cfg
    assert "token_env" in ig_cfg
