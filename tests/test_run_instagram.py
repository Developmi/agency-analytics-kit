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
    """_fetch_window_metrics makes 2 calls (reach ts + views/profile_views tv),
    then _fetch_follower_count makes 1 call = 3 total."""
    reach_response = {
        "data": [
            {
                "name": "reach",
                "period": "day",
                "values": [{"value": 1000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }
    tv_response = {
        "data": [
            {"name": "views", "period": "day", "total_value": {"value": 2500}},
            {"name": "profile_views", "period": "day", "total_value": {"value": 150}},
        ]
    }
    fc_response = {
        "data": [
            {
                "name": "follower_count",
                "period": "day",
                "values": [{"value": 5000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    call_count = 0
    captured_params_list = []

    def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        captured_params_list.append(params or {})
        if call_count == 1:
            return _mock_json(reach_response)
        elif call_count == 2:
            return _mock_json(tv_response)
        return _mock_json(fc_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token", insights_days_back=10)
    rows = list(source.resources["insights_daily"])

    assert len(rows) == 1
    assert rows[0]["report_date"] == "2024-01-01"
    assert rows[0]["reach"] == 1000
    assert rows[0]["views"] == 2500
    assert rows[0]["profile_views"] == 150
    assert rows[0]["follower_count"] == 5000

    # Verify 3 calls: reach + total_value (in _fetch_window_metrics) + follower_count
    assert len(captured_params_list) == 3
    # Call 1: reach (time_series)
    assert captured_params_list[0]["metric_type"] == "time_series"
    assert captured_params_list[0]["metric"] == "reach"
    assert "since" in captured_params_list[0]
    assert "until" in captured_params_list[0]
    # Call 2: views,profile_views (total_value)
    assert captured_params_list[1]["metric_type"] == "total_value"
    assert "views" in captured_params_list[1]["metric"]
    assert "profile_views" in captured_params_list[1]["metric"]
    # Call 3: follower_count (time_series)
    assert captured_params_list[2]["metric_type"] == "time_series"
    assert captured_params_list[2]["metric"] == "follower_count"


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


def test_media_media_url_and_new_fields(monkeypatch):
    """RED: media_url MUST be persisted + 6 new fields in yielded dict."""
    media_response = {
        "data": [
            {
                "id": "media_u1",
                "caption": "With URL",
                "media_type": "IMAGE",
                "media_url": "https://ig.com/p/media_u1/img.jpg",
                "permalink": "https://instagram.com/p/media_u1",
                "like_count": 10,
                "comments_count": 3,
                "timestamp": "2024-01-01T12:00:00+0000",
                "thumbnail_url": "https://ig.com/p/media_u1/thumb.jpg",
                "shortcode": "ABC123",
                "media_product_type": "FEED",
                "is_comment_enabled": True,
                "owner": {"id": "owner_456"},
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
    item = items[0]
    # The core fix — media_url was previously dropped
    assert item["media_url"] == "https://ig.com/p/media_u1/img.jpg"
    # New fields
    assert item["thumbnail_url"] == "https://ig.com/p/media_u1/thumb.jpg"
    assert item["shortcode"] == "ABC123"
    assert item["media_product_type"] == "FEED"
    assert item["owner_id"] == "owner_456"
    assert item["is_comment_enabled"] is True


def test_media_media_url_nullable_fields(monkeypatch):
    """TRIANGULATE: Nullable fields (caption=null, video_title=null) still work."""
    media_response = {
        "data": [
            {
                "id": "media_n1",
                "caption": None,
                "media_type": "VIDEO",
                "media_url": "https://ig.com/p/media_n1/vid.mp4",
                "permalink": "https://instagram.com/p/media_n1",
                "like_count": 5,
                "comments_count": 0,
                "timestamp": "2024-06-15T12:00:00+0000",
                "thumbnail_url": "https://ig.com/p/media_n1/thumb.jpg",
                "shortcode": "DEF456",
                "media_product_type": "REELS",
                "is_comment_enabled": False,
                "owner": {"id": "owner_789"},
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
    item = items[0]
    assert item["caption"] is None
    assert item["media_url"] == "https://ig.com/p/media_n1/vid.mp4"
    assert item["thumbnail_url"] == "https://ig.com/p/media_n1/thumb.jpg"
    assert item["shortcode"] == "DEF456"
    assert item["media_product_type"] == "REELS"
    assert item["owner_id"] == "owner_789"
    assert item["is_comment_enabled"] is False


def test_insights_dual_format_merge(monkeypatch):
    """RED: IG dual-format merge — time_series + total_value metrics by date."""
    reach_response = {
        "data": [
            {
                "name": "reach",
                "period": "day",
                "values": [{"value": 1000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }
    tv_response = {
        "data": [
            {"name": "views", "period": "day", "total_value": {"value": 2500}},
            {"name": "profile_views", "period": "day", "total_value": {"value": 150}},
            {"name": "likes", "period": "day", "total_value": {"value": 200}},
            {"name": "comments", "period": "day", "total_value": {"value": 50}},
            {"name": "shares", "period": "day", "total_value": {"value": 25}},
            {"name": "saves", "period": "day", "total_value": {"value": 30}},
            {"name": "total_interactions", "period": "day", "total_value": {"value": 500}},
            {"name": "accounts_engaged", "period": "day", "total_value": {"value": 100}},
            {"name": "website_clicks", "period": "day", "total_value": {"value": 10}},
        ]
    }
    fc_response = {
        "data": [
            {
                "name": "follower_count",
                "period": "day",
                "values": [{"value": 5000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    call_count = 0

    def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_json(reach_response)
        elif call_count == 2:
            return _mock_json(tv_response)
        return _mock_json(fc_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token", insights_days_back=10)
    rows = list(source.resources["insights_daily"])

    assert len(rows) == 1
    row = rows[0]
    assert row["report_date"] == "2024-01-01"
    # Existing metrics
    assert row["reach"] == 1000
    assert row["views"] == 2500
    assert row["profile_views"] == 150
    assert row["follower_count"] == 5000
    # NEW total_value metrics
    assert row["likes"] == 200
    assert row["comments"] == 50
    assert row["shares"] == 25
    assert row["saves"] == 30
    assert row["total_interactions"] == 500
    assert row["accounts_engaged"] == 100
    assert row["website_clicks"] == 10


def test_insights_dual_format_empty_metrics(monkeypatch):
    """TRIANGULATE: Unavailable total_value metrics → NULL in output."""
    reach_response = {
        "data": [
            {
                "name": "reach",
                "period": "day",
                "values": [{"value": 0, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }
    tv_response = {"data": []}  # Empty array — all new metrics unavailable
    fc_response = {
        "data": [
            {
                "name": "follower_count",
                "period": "day",
                "values": [{"value": 5000, "end_time": "2024-01-01T08:00:00+0000"}],
            },
        ]
    }

    call_count = 0

    def mock_get(url, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_json(reach_response)
        elif call_count == 2:
            return _mock_json(tv_response)
        return _mock_json(fc_response)

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import instagram_source

    source = instagram_source("test_biz_456", "mock_token", insights_days_back=10)
    rows = list(source.resources["insights_daily"])

    assert len(rows) == 1
    row = rows[0]
    assert row["report_date"] == "2024-01-01"
    assert row["reach"] == 0
    assert row["follower_count"] == 5000
    # All total_value metrics should be None (unavailable)
    assert row["views"] is None
    assert row["profile_views"] is None
    assert row["likes"] is None
    assert row["comments"] is None
    assert row["shares"] is None
    assert row["saves"] is None
    assert row["total_interactions"] is None
    assert row["accounts_engaged"] is None
    assert row["website_clicks"] is None


def test_business_profile_daily_guard_skips_same_day(monkeypatch):
    """RED: Business profile daily guard skips 2nd same-day call."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    class FakeDltCurrent:
        _state = {"last_run": today}

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "ig_biz_456",
        "username": "testbiz",
        "name": "Test Business",
        "profile_picture_url": "https://ig.com/pic.jpg",
        "biography": "A test business",
        "website": "https://testbiz.com",
        "followers_count": 5000,
        "follows_count": 100,
        "media_count": 200,
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import get_business_profile

    resource = get_business_profile("ig_biz_456", "mock_token")
    results = list(resource)

    assert len(results) == 0, "Expected no yield when already fetched today"


def test_business_profile_daily_guard_fetches_new_day(monkeypatch):
    """TRIANGULATE: Business profile yields data when last_run is yesterday."""

    class FakeDltCurrent:
        _state = {"last_run": "2026-07-27"}  # yesterday

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "ig_biz_456",
        "username": "testbiz",
        "name": "Test Business",
        "profile_picture_url": "https://ig.com/pic.jpg",
        "biography": "A test business",
        "website": "https://testbiz.com",
        "followers_count": 5000,
        "follows_count": 100,
        "media_count": 200,
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    from run_instagram import get_business_profile

    resource = get_business_profile("ig_biz_456", "mock_token")
    results = list(resource)

    assert len(results) == 1
    profile = results[0]
    assert profile["ig_id"] == "ig_biz_456"
    assert profile["username"] == "testbiz"
    assert profile["name"] == "Test Business"
    assert profile["profile_picture_url"] == "https://ig.com/pic.jpg"
    assert profile["biography"] == "A test business"
    assert profile["website"] == "https://testbiz.com"
    assert profile["followers_count"] == 5000
    assert profile["follows_count"] == 100
    assert profile["media_count"] == 200


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    ig_cfg = template["connectors"]["instagram"]
    assert "instagram_business_id" in ig_cfg
    assert "token_env" in ig_cfg


def test_connector_insights_days_back():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    ig_cfg = template["connectors"]["instagram"]
    assert "insights_days_back" in ig_cfg
    assert isinstance(ig_cfg["insights_days_back"], int)
