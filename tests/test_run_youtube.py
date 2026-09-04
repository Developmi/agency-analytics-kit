from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from run_youtube import (
    _analytics_query,
    _api_get,
    _handle_response,
    _parse_iso_duration,
    _TokenManager,
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


class TestTokenManager:
    @patch("run_youtube.requests.post")
    @patch("run_youtube.time.time")
    def test_refresh_and_cache(self, mock_time, mock_post):
        mock_time.return_value = 1000.0
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"access_token": "tok1", "expires_in": 3600}
        mock_post.return_value = mock_resp

        manager = _TokenManager("client-id", "client-secret", "refresh-token")
        assert manager.get_token() == "tok1"
        assert manager.get_token() == "tok1"
        mock_post.assert_called_once()

        mock_time.return_value = 1000.0 + 3600
        mock_resp.json.return_value = {"access_token": "tok2", "expires_in": 3600}
        assert manager.get_token() == "tok2"
        assert mock_post.call_count == 2

    def test_refresh_failure_raises(self):
        with patch("run_youtube.requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {"error": "invalid_grant"}
            mock_post.return_value = mock_resp

            manager = _TokenManager("client-id", "client-secret", "refresh-token")
            with pytest.raises(Exception, match="invalid_grant"):
                manager.get_token()

    @patch("run_youtube.requests.get")
    def test_401_refresh_and_retry_once(self, mock_get):
        manager = _TokenManager("client-id", "client-secret", "refresh-token")
        with patch.object(manager, "get_token", side_effect=["expired", "fresh"]):
            err_resp = MagicMock()
            err_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
            ok_resp = MagicMock()
            ok_resp.json.return_value = {
                "columnHeaders": [{"name": "video"}, {"name": "views"}],
                "rows": [["v1", "31"]],
            }
            mock_get.side_effect = [err_resp, ok_resp]

            rows = _analytics_query(
                manager, "UC_test", "2026-01-01", "2026-01-02", "video", "views", "test"
            )
            assert rows == [{"video_id": "v1", "views": "31"}]

    @patch("run_youtube.requests.get")
    def test_401_without_token_manager_raises(self, mock_get):
        err_resp = MagicMock()
        err_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
        mock_get.return_value = err_resp

        with pytest.raises(Exception, match="Invalid Credentials"):
            _api_get("https://example.com", {"part": "snippet"}, "test", api_key="key")


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
                    "contentDetails": {"relatedPlaylists": {"uploads": "UU_test_uploads"}},
                    "status": {"privacyStatus": "public"},
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
        assert row["uploads_playlist_id"] == "UU_test_uploads"
        assert row["privacy_status"] == "public"
        assert row["title"] == "Test Channel"

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
        playlist_resp = MagicMock()
        playlist_resp.json.return_value = {
            "items": [
                {"contentDetails": {"videoId": "v1"}},
                {"contentDetails": {"videoId": "v2"}},
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
                        "description": "desc one",
                    },
                    "statistics": {
                        "viewCount": "5000",
                        "likeCount": "300",
                        "commentCount": "50",
                    },
                    "contentDetails": {"duration": "PT10M30S"},
                    "status": {"privacyStatus": "public", "license": "youtube"},
                    "topicDetails": {"topicCategories": ["https://en.wikipedia.org/wiki/Foo"]},
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
                    "status": {},
                    "topicDetails": {},
                },
            ]
        }

        mock_get.side_effect = [playlist_resp, stats_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 2

        assert rows[0]["video_id"] == "v1"
        assert rows[0]["title"] == "Video One"
        assert rows[0]["view_count"] == 5000
        assert rows[0]["like_count"] == 300
        assert rows[0]["comment_count"] == 50
        assert rows[0]["dislike_count"] == 0
        assert rows[0]["duration"] == "PT10M30S"
        assert rows[0]["category_id"] == "22"
        assert rows[0]["description"] == "desc one"
        assert rows[0]["privacy_status"] == "public"
        assert rows[0]["license"] == "youtube"
        assert rows[0]["topic_categories"] == ["https://en.wikipedia.org/wiki/Foo"]

        assert rows[1]["video_id"] == "v2"
        assert rows[1]["duration"] == "PT5M15S"
        assert rows[1]["category_id"] == "23"
        assert rows[1]["privacy_status"] is None

    @patch("run_youtube.requests.get")
    def test_pagination(self, mock_get):
        page1_resp = MagicMock()
        page1_resp.json.return_value = {
            "items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}],
            "nextPageToken": "token_page_2",
        }

        page2_resp = MagicMock()
        page2_resp.json.return_value = {
            "items": [{"contentDetails": {"videoId": "v3"}}],
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

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 3
        assert [r["video_id"] for r in rows] == ["v1", "v2", "v3"]

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_enumeration(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.json.return_value = {"items": []}

        mock_get.side_effect = [rate_resp, ok_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 0

    @patch("run_youtube.requests.get")
    def test_rate_limit_during_stats(self, mock_get):
        playlist_resp = MagicMock()
        playlist_resp.json.return_value = {"items": [{"contentDetails": {"videoId": "v1"}}]}

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

        mock_get.side_effect = [playlist_resp, rate_resp, stats_resp]

        rows = list(get_videos("UC_test", "UU_test", "key"))
        assert len(rows) == 1

    @patch("run_youtube.requests.get")
    def test_no_videos_found(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"items": []}
        mock_get.return_value = mock_resp

        rows = list(get_videos("UC_empty", "UU_empty", "key"))
        assert len(rows) == 0


class TestVideoDailyAnalytics:
    @patch("run_youtube.date")
    def test_video_daily_analytics_success(self, mock_date):
        mock_date.today.return_value = date(2026, 7, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        manager = _TokenManager("client-id", "client-secret", "refresh-token")

        with (
            patch.object(manager, "get_token", return_value="tok"),
            patch("run_youtube.requests.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "columnHeaders": [
                    {"name": "day", "columnType": "DIMENSION"},
                    {"name": "views", "columnType": "METRIC"},
                    {"name": "estimatedMinutesWatched", "columnType": "METRIC"},
                    {"name": "averageViewDuration", "columnType": "METRIC"},
                ],
                "rows": [["2026-07-01", "31", "40", "77.4"]],
            }
            mock_get.return_value = mock_resp

            rows = list(get_video_daily_analytics("UC_test", manager, "2026-07-01"))
            assert len(rows) == 1
            row = rows[0]
            assert row["report_date"] == "2026-07-01"
            assert row["views"] == "31"
            assert row["estimated_minutes_watched"] == "40"
            assert row["average_view_duration"] == "77.4"

    @patch("run_youtube.date")
    def test_empty_window_skipped(self, mock_date):
        mock_date.today.return_value = date(2026, 7, 22)
        mock_date.fromisoformat.side_effect = date.fromisoformat
        manager = _TokenManager("client-id", "client-secret", "refresh-token")

        with (
            patch.object(manager, "get_token", return_value="tok"),
            patch("run_youtube.requests.get") as mock_get,
        ):
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "error": {"code": 400, "message": "The query did not return any data."}
            }
            mock_get.return_value = mock_resp

            rows = list(get_video_daily_analytics("UC_test", manager, "2026-07-01"))
            assert rows == []


class TestYoutubeSource:
    @patch("run_youtube.requests.get")
    def test_source_returns_public_resources(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {
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
        mock_get.return_value = channel_resp

        source = youtube_source("UC_test", "key")
        resources = list(source.resources.values())
        names = [r.name for r in resources]
        assert len(resources) == 8
        assert set(names) == {
            "channel_stats",
            "uploaded_videos",
            "videos",
            "playlists",
            "playlist_items",
            "comment_threads",
            "channel_sections",
            "video_categories",
        }

    @patch("run_youtube.requests.get")
    def test_source_with_oauth_includes_analytics(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        oauth_config = {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
        source = youtube_source("UC_test", "key", oauth_config=oauth_config)
        names = [r.name for r in source.resources.values()]
        assert len(names) == 13
        assert "video_daily_analytics" in names
        assert "video_analytics" in names
        assert "traffic_source_analytics" in names
        assert "device_analytics" in names
        assert "country_analytics" in names
        assert "captions" not in names

    @patch("run_youtube.requests.get")
    def test_source_with_captions_enabled(self, mock_get):
        channel_resp = MagicMock()
        channel_resp.json.return_value = {"items": []}
        mock_get.return_value = channel_resp

        oauth_config = {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "refresh_token": "refresh-token",
        }
        source = youtube_source("UC_test", "key", oauth_config=oauth_config, captions_enabled=True)
        names = [r.name for r in source.resources.values()]
        assert "captions" in names


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

    def test_handle_response_401_raises_auth_error(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 401, "message": "Invalid Credentials"}}
        with pytest.raises(Exception, match="Invalid Credentials"):
            _handle_response(mock_resp, "test")

    def test_handle_response_429_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"error": {"code": 429, "message": "Rate limit"}}
        mock_resp.headers = {"Retry-After": "5"}
        result = _handle_response(mock_resp, "test")
        assert result is None
