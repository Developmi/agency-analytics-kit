from unittest.mock import MagicMock, patch

import pytest
from run_youtube import (
    _handle_response,
    _parse_iso_duration,
    get_channel_stats,
    get_video_daily_analytics,
    get_videos,
    youtube_source,
)


class TestParseIsoDuration:
    def test_full_duration(self):
        assert _parse_iso_duration("PT1H30M15S") == 5415

    def test_minutes_only(self):
        assert _parse_iso_duration("PT5M") == 300

    def test_seconds_only(self):
        assert _parse_iso_duration("PT45S") == 45

    def test_hours_only(self):
        assert _parse_iso_duration("PT2H") == 7200

    def test_empty_fallback(self):
        assert _parse_iso_duration("") == 0

    def test_no_match(self):
        assert _parse_iso_duration("P1DT2H") == 0


class TestChannelStats:
    @patch("run_youtube.requests.get")
    @patch("run_youtube.date")
    def test_channel_stats_success(self, mock_date, mock_get):
        mock_date.today.return_value.isoformat.return_value = "2026-07-22"
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {
                        "title": "Test Channel",
                        "description": "A test channel",
                        "publishedAt": "2020-01-01T00:00:00Z",
                    },
                    "statistics": {
                        "subscriberCount": "1500",
                        "viewCount": "75000",
                        "videoCount": "200",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }
        mock_get.return_value = mock_resp

        rows = list(get_channel_stats("UC_test", "fake-key"))
        assert len(rows) == 1
        row = rows[0]
        assert row["report_date"] == "2026-07-22"
        assert row["subscriber_count"] == 1500
        assert row["view_count"] == 75000
        assert row["video_count"] == 200
        assert row["hidden_subscriber_count"] is False

    @patch("run_youtube.requests.get")
    def test_rate_limit_retry(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate Limit Exceeded"}}
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {},
                    "statistics": {
                        "subscriberCount": "100",
                        "viewCount": "500",
                        "videoCount": "10",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }

        mock_get.side_effect = [rate_resp, ok_resp]
        rows = list(get_channel_stats("UC_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.time.sleep")
    @patch("run_youtube.requests.get")
    def test_rate_limit_quota_exceeded(self, mock_get, mock_sleep):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {
            "error": {"code": 403, "message": "quotaExceeded – rate limiting"}
        }

        ok_resp = MagicMock()
        ok_resp.json.return_value = {
            "items": [
                {
                    "id": "UC_test",
                    "snippet": {},
                    "statistics": {
                        "subscriberCount": "100",
                        "viewCount": "500",
                        "videoCount": "10",
                        "hiddenSubscriberCount": False,
                    },
                }
            ]
        }

        mock_get.side_effect = [rate_resp, ok_resp]
        rows = list(get_channel_stats("UC_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.requests.get")
    def test_invalid_key(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 400, "message": "Bad Request"}}
        mock_get.return_value = mock_resp

        with pytest.raises(Exception, match="Bad request"):
            list(get_channel_stats("UC_test", "bad-key"))

    @patch("run_youtube.requests.get")
    def test_no_channel_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_channel_stats("UC_nonexistent", "key"))
        assert len(rows) == 0


class TestVideos:
    @patch("run_youtube.requests.get")
    def test_videos_success(self, mock_get):
        search_resp = MagicMock()
        search_resp.json.return_value = {
            "items": [
                {"id": {"videoId": "v1"}},
                {"id": {"videoId": "v2"}},
            ]
        }

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": "v1",
                    "snippet": {
                        "title": "Video One",
                        "publishedAt": "2024-06-01T10:00:00Z",
                        "categoryId": "22",
                    },
                    "statistics": {
                        "viewCount": "5000",
                        "likeCount": "300",
                        "commentCount": "50",
                    },
                    "contentDetails": {"duration": "PT10M30S"},
                },
                {
                    "id": "v2",
                    "snippet": {
                        "title": "Video Two",
                        "publishedAt": "2024-06-02T12:00:00Z",
                        "categoryId": "23",
                    },
                    "statistics": {
                        "viewCount": "3000",
                        "likeCount": "150",
                        "commentCount": "20",
                    },
                    "contentDetails": {"duration": "PT5M15S"},
                },
            ]
        }

        mock_get.side_effect = [search_resp, stats_resp]

        rows = list(get_videos("UC_test", "key"))
        assert len(rows) == 2

        assert rows[0]["video_id"] == "v1"
        assert rows[0]["title"] == "Video One"
        assert rows[0]["view_count"] == 5000
        assert rows[0]["like_count"] == 300
        assert rows[0]["comment_count"] == 50
        assert rows[0]["duration"] == "PT10M30S"
        assert rows[0]["category_id"] == "22"

        assert rows[1]["video_id"] == "v2"
        assert rows[1]["duration"] == "PT5M15S"
        assert rows[1]["category_id"] == "23"

    @patch("run_youtube.requests.get")
    def test_pagination(self, mock_get):
        page1_resp = MagicMock()
        page1_resp.json.return_value = {
            "items": [{"id": {"videoId": "v1"}}, {"id": {"videoId": "v2"}}],
            "nextPageToken": "token_page_2",
        }

        page2_resp = MagicMock()
        page2_resp.json.return_value = {
            "items": [{"id": {"videoId": "v3"}}],
        }

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": f"v{i}",
                    "snippet": {"title": f"V{i}", "publishedAt": "2024-01-01"},
                    "statistics": {
                        "viewCount": str(i * 100),
                        "likeCount": str(i * 10),
                        "commentCount": str(i * 5),
                    },
                    "contentDetails": {"duration": "PT5M"},
                }
                for i in range(1, 4)
            ]
        }

        mock_get.side_effect = [page1_resp, page2_resp, stats_resp]

        rows = list(get_videos("UC_test", "key"))
        assert len(rows) == 3
        assert [r["video_id"] for r in rows] == ["v1", "v2", "v3"]

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_search(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.json.return_value = {"items": []}

        mock_get.side_effect = [rate_resp, ok_resp]

        rows = list(get_videos("UC_test", "key"))
        assert len(rows) == 0

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_stats(self, mock_get):
        search_resp = MagicMock()
        search_resp.json.return_value = {"items": [{"id": {"videoId": "v1"}}]}

        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        rate_resp.headers = {"Retry-After": "1"}

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": "v1",
                    "snippet": {"title": "V1", "publishedAt": "2024-01-01"},
                    "statistics": {
                        "viewCount": "100",
                        "likeCount": "10",
                        "commentCount": "5",
                    },
                    "contentDetails": {"duration": "PT5M"},
                }
            ]
        }

        mock_get.side_effect = [search_resp, rate_resp, stats_resp]

        rows = list(get_videos("UC_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.requests.get")
    def test_no_videos_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_videos("UC_empty", "key"))
        assert len(rows) == 0


class TestVideoDailyAnalytics:
    @patch("run_youtube.requests.get")
    @patch("run_youtube.date")
    def test_video_daily_analytics_success(self, mock_date, mock_get):
        mock_date.today.return_value.isoformat.return_value = "2026-07-22"

        search_resp = MagicMock()
        search_resp.json.return_value = {"items": [{"id": {"videoId": "v1"}}]}

        stats_resp = MagicMock()
        stats_resp.json.return_value = {
            "items": [
                {
                    "id": "v1",
                    "statistics": {"viewCount": "5000"},
                    "contentDetails": {"duration": "PT10M30S"},
                }
            ]
        }

        mock_get.side_effect = [search_resp, stats_resp]

        rows = list(get_video_daily_analytics("UC_test", "key"))
        assert len(rows) == 1
        row = rows[0]
        assert row["report_date"] == "2026-07-22"
        assert row["video_id"] == "v1"
        assert row["views"] == 5000
        assert row["estimated_minutes_watched"] == pytest.approx(10.5, rel=0.01)
        assert row["average_view_duration_seconds"] == pytest.approx(630, rel=0.01)

    @patch("run_youtube.requests.get")
    def test_no_videos(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_video_daily_analytics("UC_empty", "key"))
        assert len(rows) == 0


class TestYoutubeSource:
    @patch("run_youtube.requests.get")
    @patch("run_youtube.date")
    def test_source_returns_all_resources(self, mock_date, mock_get):
        mock_date.today.return_value.isoformat.return_value = "2026-07-22"

        def mock_responses(*args, **kwargs):
            url = kwargs.get("url") or args[0] if args else ""
            params = kwargs.get("params") or {}
            if "/channels" in url:
                resp = MagicMock()
                resp.json.return_value = {
                    "items": [
                        {
                            "id": "UC_test",
                            "snippet": {},
                            "statistics": {
                                "subscriberCount": "100",
                                "viewCount": "500",
                                "videoCount": "10",
                                "hiddenSubscriberCount": False,
                            },
                        }
                    ]
                }
                return resp
            elif "/search" in url:
                resp = MagicMock()
                if params.get("pageToken"):
                    resp.json.return_value = {"items": []}
                else:
                    resp.json.return_value = {"items": [{"id": {"videoId": "v1"}}]}
                return resp
            elif "/videos" in url:
                resp = MagicMock()
                resp.json.return_value = {
                    "items": [
                        {
                            "id": "v1",
                            "snippet": {
                                "title": "V1",
                                "publishedAt": "2024-01-01",
                            },
                            "statistics": {
                                "viewCount": "100",
                                "likeCount": "10",
                                "commentCount": "5",
                            },
                            "contentDetails": {"duration": "PT5M"},
                        }
                    ]
                }
                return resp
            resp = MagicMock()
            resp.json.return_value = {"items": []}
            return resp

        mock_get.side_effect = mock_responses

        source = youtube_source("UC_test", "key")
        resources = list(source.resources.values())
        assert len(resources) == 3

        names = [r.name for r in resources]
        assert "channel_stats" in names
        assert "videos" in names
        assert "video_daily_analytics" in names


class TestHandleResponse:
    def test_handle_response_valid(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": [{"id": "test"}]}
        result = _handle_response(mock_resp, "test")
        assert result == {"items": [{"id": "test"}]}

    def test_handle_response_400(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 400, "message": "Bad Request"}}
        with pytest.raises(Exception, match="Bad request"):
            _handle_response(mock_resp, "test")

    def test_handle_response_429_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        mock_resp.headers = {"Retry-After": "5"}
        result = _handle_response(mock_resp, "test")
        assert result is None
