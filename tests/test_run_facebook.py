import os

import pytest
import yaml

MODULE = "run_facebook"


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def test_page_posts_success(monkeypatch):
    posts_response = {
        "data": [
            {
                "id": "post_1",
                "message": "Hello world",
                "created_time": "2024-01-01T12:00:00+0000",
                "permalink_url": "https://fb.com/post_1",
                "story": "Test story",
                "likes": {"summary": {"total_count": 10}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 2},
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(posts_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    assert posts[0]["post_id"] == "post_1"
    assert posts[0]["message"] == "Hello world"
    assert posts[0]["likes_count"] == 10
    assert posts[0]["comments_count"] == 5
    assert posts[0]["shares_count"] == 2
    assert posts[0]["story"] == "Test story"
    assert posts[0]["permalink_url"] == "https://fb.com/post_1"
    assert posts[0]["created_time"] == "2024-01-01T12:00:00+0000"


def test_page_feed_success(monkeypatch):
    feed_response = {
        "data": [
            {
                "id": "feed_1",
                "message": "Feed post content",
                "created_time": "2024-01-02T14:00:00+0000",
                "permalink_url": "https://fb.com/feed_1",
                "story": "Feed story",
                "from": {"id": "page_123", "name": "Test Page"},
                "likes": {"summary": {"total_count": 20}},
                "comments": {"summary": {"total_count": 7}},
                "shares": {"count": 3},
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(feed_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    feed = list(source.resources["feed"])

    assert len(feed) == 1
    assert feed[0]["feed_item_id"] == "feed_1"
    assert feed[0]["message"] == "Feed post content"
    assert feed[0]["author_id"] == "page_123"
    assert feed[0]["author_name"] == "Test Page"
    assert feed[0]["likes_count"] == 20
    assert feed[0]["comments_count"] == 7
    assert feed[0]["shares_count"] == 3
    assert feed[0]["story"] == "Feed story"
    assert feed[0]["permalink_url"] == "https://fb.com/feed_1"


def test_page_insights_success(monkeypatch):
    insights_response = {
        "data": [
            {
                "name": "page_total_media_view_unique",
                "period": "day",
                "values": [{"value": 300, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_media_view",
                "period": "day",
                "values": [{"value": 500, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_video_views",
                "period": "day",
                "values": [{"value": 50, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_views_total",
                "period": "day",
                "values": [{"value": 200, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_follows",
                "period": "day",
                "values": [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_total_actions",
                "period": "day",
                "values": [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    mock_get = lambda url, params=None: _mock_json(insights_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    rows = list(source.resources["page_insights_daily"])

    assert len(rows) == 1
    assert rows[0]["report_date"] == "2024-01-01"
    assert rows[0]["page_total_media_view_unique"] == 300
    assert rows[0]["page_media_view"] == 500
    assert rows[0]["page_video_views"] == 50
    assert rows[0]["page_views_total"] == 200
    assert rows[0]["page_daily_follows"] == 5
    assert rows[0]["page_total_actions"] == 2


def test_rate_limit_retry(monkeypatch):
    calls = []

    def mock_get(url, params=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 4, "message": "Rate limit"}})
        return _mock_json(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "ok",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            }
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    assert posts[0]["post_id"] == "post_1"
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 190, "message": "Token expired"}}
    monkeypatch.setattr(f"{MODULE}.requests.get", lambda url, params=None: _mock_json(error_data))

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    with pytest.raises(Exception, match="Invalid or expired token"):
        list(source.resources["page_posts"])


def test_pagination(monkeypatch):
    responses = [
        _mock_json(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "first page",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": "https://graph.facebook.com/next_page"},
            }
        ),
        _mock_json(
            {
                "data": [
                    {
                        "id": "post_2",
                        "message": "second page",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            }
        ),
    ]

    monkeypatch.setattr(f"{MODULE}.requests.get", lambda url, params=None: responses.pop(0))

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 2
    assert posts[0]["message"] == "first page"
    assert posts[1]["message"] == "second page"


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    fb_cfg = template["connectors"]["facebook"]
    assert "page_id" in fb_cfg
    assert "token_env" in fb_cfg
