import os

import pytest
import yaml

MODULE = "run_tiktok_organic"


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def test_profile_stats_success(monkeypatch):
    stats_response = {
        "data": {
            "user": {
                "follower_count": 10000,
                "following_count": 500,
                "likes_count": 50000,
                "video_count": 200,
            }
        }
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get", lambda url, params=None, headers=None: _mock_json(stats_response)
    )

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_789", "mock_token")
    rows = list(source.resources["profile_stats"])

    assert len(rows) == 1
    assert rows[0]["follower_count"] == 10000
    assert rows[0]["following_count"] == 500
    assert rows[0]["total_likes"] == 50000
    assert rows[0]["total_videos"] == 200
    assert rows[0]["report_date"] is not None


def test_videos_success(monkeypatch):
    videos_response = {
        "data": {
            "videos": [
                {
                    "id": "video_1",
                    "title": "My first video",
                    "create_time": "2024-01-01T12:00:00Z",
                    "like_count": 150,
                    "comment_count": 20,
                    "share_count": 10,
                    "view_count": 5000,
                }
            ],
            "cursor": 0,
            "has_more": False,
        }
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.get", lambda url, params=None, headers=None: _mock_json(videos_response)
    )

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_789", "mock_token")
    items = list(source.resources["videos_organic"])

    assert len(items) == 1
    assert items[0]["video_id"] == "video_1"
    assert items[0]["title"] == "My first video"
    assert items[0]["like_count"] == 150
    assert items[0]["comment_count"] == 20
    assert items[0]["share_count"] == 10
    assert items[0]["view_count"] == 5000


def test_rate_limit(monkeypatch):
    calls = []

    def mock_get(url, params=None, headers=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 40004, "message": "Rate limit"}})
        return _mock_json(
            {
                "data": {
                    "user": {
                        "follower_count": 100,
                        "following_count": 10,
                        "likes_count": 500,
                        "video_count": 5,
                    }
                }
            }
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_789", "mock_token")
    rows = list(source.resources["profile_stats"])

    assert len(rows) == 1
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 40007, "message": "Token expired"}}
    monkeypatch.setattr(
        f"{MODULE}.requests.get", lambda url, params=None, headers=None: _mock_json(error_data)
    )

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_789", "mock_token")
    with pytest.raises(Exception, match="Token expired"):
        list(source.resources["profile_stats"])


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    tt_cfg = template["connectors"]["tiktok_organic"]
    assert "open_id" in tt_cfg
    assert "token_env" in tt_cfg
