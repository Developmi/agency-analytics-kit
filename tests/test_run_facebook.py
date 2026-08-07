import json
import os
from datetime import datetime, timezone

import pytest
import yaml

MODULE = "run_facebook"


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def _batch_response(core_body, engagement_body):
    return _mock_json([
        {"code": 200, "body": json.dumps(core_body)},
        {"code": 200, "body": json.dumps(engagement_body)},
    ])


def test_page_posts_success(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_1",
                "message": "Hello world",
                "created_time": "2024-01-01T12:00:00+0000",
                "permalink_url": "https://fb.com/post_1",
                "story": "Test story",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_1",
                "likes": {"summary": {"total_count": 10}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 2},
                "r_like": {"summary": {"total_count": 10}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

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
    core_body = {
        "data": [
            {
                "id": "feed_1",
                "message": "Feed post content",
                "created_time": "2024-01-02T14:00:00+0000",
                "permalink_url": "https://fb.com/feed_1",
                "story": "Feed story",
                "from": {"id": "page_123", "name": "Test Page"},
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "feed_1",
                "likes": {"summary": {"total_count": 20}},
                "comments": {"summary": {"total_count": 7}},
                "shares": {"count": 3},
                "r_like": {"summary": {"total_count": 20}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

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

    captured_params = {}

    def mock_get(url, params=None):
        captured_params.update(params or {})
        return _mock_json(insights_response)

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

    assert "since" in captured_params
    assert "until" in captured_params


def test_rate_limit_retry(monkeypatch):
    calls = []

    def mock_post(url, params=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 4, "message": "Rate limit"}})
        return _batch_response(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "ok",
                    }
                ],
                "paging": {"next": None},
            },
            {
                "data": [
                    {
                        "id": "post_1",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                "r_like": {"summary": {"total_count": 0}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
                },
            )

    monkeypatch.setattr(f"{MODULE}.requests.post", mock_post)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    assert posts[0]["post_id"] == "post_1"
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 190, "message": "Token expired"}}
    monkeypatch.setattr(
        f"{MODULE}.requests.post", lambda url, params=None: _mock_json(error_data)
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    with pytest.raises(Exception, match="Invalid or expired token"):
        list(source.resources["page_posts"])


def test_pagination(monkeypatch):
    responses = [
        _batch_response(
            {
                "data": [
                    {
                        "id": "post_1",
                        "message": "first page",
                    }
                ],
                "paging": {
                    "next": (
                        "https://graph.facebook.com/v25.0/test_page_123/posts"
                        "?fields=...&limit=50&after=cursor2"
                    )
                },
            },
            {
                "data": [
                    {
                        "id": "post_1",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                        "r_like": {"summary": {"total_count": 0}},
                        "r_love": {"summary": {"total_count": 0}},
                        "r_wow": {"summary": {"total_count": 0}},
                        "r_haha": {"summary": {"total_count": 0}},
                        "r_sad": {"summary": {"total_count": 0}},
                        "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
                },
            ),
        _batch_response(
            {
                "data": [
                    {
                        "id": "post_2",
                        "message": "second page",
                    }
                ],
                "paging": {"next": None},
            },
            {
                "data": [
                    {
                        "id": "post_2",
                        "likes": {"summary": {"total_count": 0}},
                        "comments": {"summary": {"total_count": 0}},
                        "r_like": {"summary": {"total_count": 0}},
                        "r_love": {"summary": {"total_count": 0}},
                        "r_wow": {"summary": {"total_count": 0}},
                        "r_haha": {"summary": {"total_count": 0}},
                        "r_sad": {"summary": {"total_count": 0}},
                        "r_angry": {"summary": {"total_count": 0}},
                    }
                ],
                "paging": {"next": None},
            },
        ),
    ]

    monkeypatch.setattr(
        f"{MODULE}.requests.post", lambda url, params=None: responses.pop(0)
    )
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

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


def test_page_posts_reaction_fields(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_r1",
                "message": "Reaction test",
                "created_time": "2024-06-01T12:00:00+0000",
                "permalink_url": "https://fb.com/post_r1",
                "story": "Test story",
                "full_picture": "https://fb.com/pic.jpg",
                "status_type": "added_photos",
                "is_published": True,
                "updated_time": "2024-06-01T13:00:00+0000",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_r1",
                "r_like": {"summary": {"total_count": 42}},
                "r_love": {"summary": {"total_count": 7}},
                "r_wow": {"summary": {"total_count": 3}},
                "r_haha": {"summary": {"total_count": 5}},
                "r_sad": {"summary": {"total_count": 1}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 42}},
                "comments": {"summary": {"total_count": 5}},
                "shares": {"count": 2},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    post = posts[0]
    assert post["full_picture"] == "https://fb.com/pic.jpg"
    assert post["r_like"] == 42
    assert post["r_love"] == 7
    assert post["r_wow"] == 3
    assert post["r_haha"] == 5
    assert post["r_sad"] == 1
    assert post["r_angry"] == 0
    assert post["status_type"] == "added_photos"
    assert post["is_published"] is True
    assert post["updated_time"] == "2024-06-01T13:00:00+0000"


def test_page_posts_reactions_zero(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "post_z1",
                "message": "Zero reactions",
                "created_time": "2024-06-02T12:00:00+0000",
                "permalink_url": "https://fb.com/post_z1",
                "status_type": None,
                "is_published": False,
                "updated_time": None,
                "full_picture": None,
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "post_z1",
                "r_like": {"summary": {"total_count": 0}},
                "r_love": {"summary": {"total_count": 0}},
                "r_wow": {"summary": {"total_count": 0}},
                "r_haha": {"summary": {"total_count": 0}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 0}},
                "comments": {"summary": {"total_count": 0}},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    posts = list(source.resources["page_posts"])

    assert len(posts) == 1
    post = posts[0]
    assert post["r_like"] == 0
    assert post["r_love"] == 0
    assert post["r_wow"] == 0
    assert post["r_haha"] == 0
    assert post["r_sad"] == 0
    assert post["r_angry"] == 0
    assert post["full_picture"] is None
    assert post["status_type"] is None
    assert post["is_published"] is False
    assert post["updated_time"] is None


def test_page_feed_reaction_fields(monkeypatch):
    core_body = {
        "data": [
            {
                "id": "feed_r1",
                "message": "Feed reaction test",
                "created_time": "2024-06-01T14:00:00+0000",
                "permalink_url": "https://fb.com/feed_r1",
                "story": "Feed story",
                "from": {"id": "page_123", "name": "Test Page"},
                "full_picture": "https://fb.com/feed_pic.jpg",
                "status_type": "added_video",
                "is_published": True,
                "updated_time": "2024-06-01T15:00:00+0000",
            }
        ],
        "paging": {"next": None},
    }
    engagement_body = {
        "data": [
            {
                "id": "feed_r1",
                "r_like": {"summary": {"total_count": 15}},
                "r_love": {"summary": {"total_count": 3}},
                "r_wow": {"summary": {"total_count": 1}},
                "r_haha": {"summary": {"total_count": 2}},
                "r_sad": {"summary": {"total_count": 0}},
                "r_angry": {"summary": {"total_count": 0}},
                "likes": {"summary": {"total_count": 15}},
                "comments": {"summary": {"total_count": 3}},
                "shares": {"count": 1},
            }
        ],
        "paging": {"next": None},
    }

    monkeypatch.setattr(
        f"{MODULE}.requests.post",
        lambda url, params=None: _batch_response(core_body, engagement_body),
    )

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    feed = list(source.resources["feed"])

    assert len(feed) == 1
    item = feed[0]
    assert item["author_id"] == "page_123"
    assert item["author_name"] == "Test Page"
    assert item["full_picture"] == "https://fb.com/feed_pic.jpg"
    assert item["r_like"] == 15
    assert item["r_love"] == 3
    assert item["r_wow"] == 1
    assert item["r_haha"] == 2
    assert item["r_sad"] == 0
    assert item["r_angry"] == 0
    assert item["status_type"] == "added_video"
    assert item["is_published"] is True
    assert item["updated_time"] == "2024-06-01T15:00:00+0000"


def test_page_insights_new_metrics(monkeypatch):
    _v = [{"value": 300, "end_time": "2024-01-01T08:00:00+0000"}]
    _v5 = [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}]
    _v2 = [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}]
    insights_response = {
        "data": [
            {"name": "page_total_media_view_unique", "period": "day", "values": _v},
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
            {"name": "page_daily_follows", "period": "day", "values": _v5},
            {"name": "page_total_actions", "period": "day", "values": _v2},
            {
                "name": "page_follows",
                "period": "day",
                "values": [{"value": 150, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_post_engagements",
                "period": "day",
                "values": [{"value": 89, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_follows_unique",
                "period": "day",
                "values": [{"value": 12, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_daily_unfollows_unique",
                "period": "day",
                "values": [{"value": 3, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_like_total",
                "period": "day",
                "values": [{"value": 30, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_love_total",
                "period": "day",
                "values": [{"value": 10, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_wow_total",
                "period": "day",
                "values": [{"value": 5, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_haha_total",
                "period": "day",
                "values": [{"value": 8, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_sorry_total",
                "period": "day",
                "values": [{"value": 2, "end_time": "2024-01-01T08:00:00+0000"}],
            },
            {
                "name": "page_actions_post_reactions_anger_total",
                "period": "day",
                "values": [{"value": 1, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    mock_get = lambda url, params=None: _mock_json(insights_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import facebook_page_source

    source = facebook_page_source("test_page_123", "mock_token")
    rows = list(source.resources["page_insights_daily"])

    assert len(rows) == 1
    row = rows[0]
    assert row["page_follows"] == 150
    assert row["page_post_engagements"] == 89
    assert row["page_daily_follows_unique"] == 12
    assert row["page_daily_unfollows_unique"] == 3
    assert row["page_actions_post_reactions_like_total"] == 30
    assert row["page_actions_post_reactions_love_total"] == 10
    assert row["page_actions_post_reactions_wow_total"] == 5
    assert row["page_actions_post_reactions_haha_total"] == 8
    assert row["page_actions_post_reactions_sorry_total"] == 2
    assert row["page_actions_post_reactions_anger_total"] == 1


def test_page_profile_daily_guard_skips_same_day(monkeypatch):
    today = datetime.now(timezone.utc).date().isoformat()

    class FakeDltCurrent:
        _state = {"last_run": today}

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "page_123",
        "fan_count": 1000,
        "followers_count": 800,
        "name": "Test Page",
        "username": "testpage",
        "picture": {"url": "https://fb.com/pic.jpg"},
        "about": "Test about",
        "website": "https://test.com",
        "verification_status": "not_verified",
        "rating_count": 42,
        "category": "Business",
        "cover": {"source": "https://fb.com/cover.jpg"},
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import get_page_profile

    resource = get_page_profile("test_page_123", "mock_token")
    results = list(resource)

    assert len(results) == 0, "Expected no yield when already fetched today"


def test_page_profile_daily_guard_fetches_new_day(monkeypatch):
    class FakeDltCurrent:
        _state = {"last_run": "2026-07-27"}  # yesterday

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "page_123",
        "fan_count": 1000,
        "followers_count": 800,
        "name": "Test Page",
        "username": "testpage",
        "picture": {"url": "https://fb.com/pic.jpg"},
        "about": "Test about",
        "website": "https://test.com",
        "verification_status": "not_verified",
        "rating_count": 42,
        "category": "Business",
        "cover": {"source": "https://fb.com/cover.jpg"},
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_facebook import get_page_profile

    resource = get_page_profile("test_page_123", "mock_token")
    results = list(resource)

    assert len(results) == 1
    profile = results[0]
    assert profile["page_id"] == "page_123"
    assert profile["fan_count"] == 1000
    assert profile["followers_count"] == 800
    assert profile["name"] == "Test Page"
    assert profile["username"] == "testpage"
    assert profile["picture_url"] == "https://fb.com/pic.jpg"
    assert profile["about"] == "Test about"
    assert profile["website"] == "https://test.com"
    assert profile["verification_status"] == "not_verified"
    assert profile["rating_count"] == 42
    assert profile["category"] == "Business"
    assert profile["cover"] == "https://fb.com/cover.jpg"


def test_connector_insights_days_back():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    fb_cfg = template["connectors"]["facebook"]
    assert "insights_days_back" in fb_cfg
    assert isinstance(fb_cfg["insights_days_back"], int)
