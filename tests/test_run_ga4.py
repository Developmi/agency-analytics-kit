from unittest.mock import MagicMock, patch

import pytest
from run_ga4 import (
    _parse_rows,
    _resolve_service_account,
    _run_report,
    ga4_source,
    get_daily_stats,
    get_event_analytics,
    get_page_analytics,
)

SAMPLE_DAILY_RESPONSE = {
    "dimensionHeaders": [{"name": "date"}],
    "metricHeaders": [
        {"name": "sessions"},
        {"name": "totalUsers"},
        {"name": "newUsers"},
        {"name": "screenPageViews"},
        {"name": "bounceRate"},
        {"name": "averageSessionDuration"},
    ],
    "rows": [
        {
            "dimensionValues": [{"value": "20260701"}],
            "metricValues": [
                {"value": "100"},
                {"value": "80"},
                {"value": "20"},
                {"value": "300"},
                {"value": "0.35"},
                {"value": "180.5"},
            ],
        },
        {
            "dimensionValues": [{"value": "20260702"}],
            "metricValues": [
                {"value": "120"},
                {"value": "95"},
                {"value": "25"},
                {"value": "350"},
                {"value": "0.30"},
                {"value": "195.2"},
            ],
        },
    ],
}

SAMPLE_PAGE_RESPONSE = {
    "dimensionHeaders": [
        {"name": "date"},
        {"name": "pagePath"},
        {"name": "pageTitle"},
    ],
    "metricHeaders": [
        {"name": "screenPageViews"},
        {"name": "totalUsers"},
        {"name": "averageSessionDuration"},
        {"name": "bounceRate"},
    ],
    "rows": [
        {
            "dimensionValues": [
                {"value": "20260701"},
                {"value": "/"},
                {"value": "Home"},
            ],
            "metricValues": [
                {"value": "150"},
                {"value": "80"},
                {"value": "120.3"},
                {"value": "0.25"},
            ],
        }
    ],
}

SAMPLE_EVENT_RESPONSE = {
    "dimensionHeaders": [{"name": "date"}, {"name": "eventName"}],
    "metricHeaders": [{"name": "eventCount"}, {"name": "totalUsers"}],
    "rows": [
        {
            "dimensionValues": [{"value": "20260701"}, {"value": "page_view"}],
            "metricValues": [{"value": "500"}, {"value": "200"}],
        },
        {
            "dimensionValues": [{"value": "20260701"}, {"value": "click"}],
            "metricValues": [{"value": "50"}, {"value": "30"}],
        },
    ],
}


@pytest.fixture(autouse=True)
def mock_ga4_auth():
    """Evita que los tests intenten usar google-auth real."""
    with patch("run_ga4._get_access_token", return_value="fake-token"):
        yield


class TestResolveServiceAccount:
    def test_base64_decode(self):
        sa_b64 = (
            "ewogICJjbGllbnRfZW1haWwiOiAidGVzdEBleGFtcGxlLmNvbSIsCiAgInByaXZhdGVfa2V5IjogIi0tLS0"
            "tQkVHSU4gUFJJVkFURSBLRVktLS0tLVxuIn0="
        )
        result = _resolve_service_account(sa_b64)
        assert result["client_email"] == "test@example.com"
        assert "PRIVATE KEY" in result["private_key"]

    def test_file_path(self, ga4_service_account_file):
        result = _resolve_service_account(ga4_service_account_file)
        assert result["client_email"] == "test@example.com"
        assert "PRIVATE KEY" in result["private_key"]

    def test_invalid_raises(self):
        with pytest.raises(Exception, match="neither a valid base64"):
            _resolve_service_account("not-valid-base64!!!")


class TestDailyStats:
    @patch("run_ga4._run_report")
    def test_daily_stats_success(self, mock_run_report):
        mock_run_report.return_value = SAMPLE_DAILY_RESPONSE

        rows = list(get_daily_stats("prop/123", "email", "key"))
        assert len(rows) == 2

        row = rows[0]
        assert row["report_date"] == "20260701"
        assert row["sessions"] == 100
        assert row["total_users"] == 80
        assert row["new_users"] == 20
        assert row["pageviews"] == 300
        assert row["bounce_rate"] == 0.35
        assert row["avg_session_duration_seconds"] == 180.5

        row2 = rows[1]
        assert row2["sessions"] == 120

    @patch("run_ga4._run_report")
    def test_rate_limit_retry(self, mock_run_report):
        mock_run_report.side_effect = [None, SAMPLE_DAILY_RESPONSE]

        rows = list(get_daily_stats("prop/123", "email", "key"))
        assert len(rows) == 2

    @patch("run_ga4._run_report")
    def test_invalid_credentials_raises(self, mock_run_report):
        mock_run_report.side_effect = Exception("[GA4] Token expired or access denied")

        with pytest.raises(Exception, match="Token expired"):
            list(get_daily_stats("prop/123", "email", "bad-key"))


class TestPageAnalytics:
    @patch("run_ga4._run_report")
    def test_page_analytics_success(self, mock_run_report):
        mock_run_report.return_value = SAMPLE_PAGE_RESPONSE

        rows = list(get_page_analytics("prop/123", "email", "key"))
        assert len(rows) == 1

        row = rows[0]
        assert row["report_date"] == "20260701"
        assert row["page_path"] == "/"
        assert row["page_title"] == "Home"
        assert row["pageviews"] == 150
        assert row["unique_pageviews"] == 80
        assert row["avg_time_on_page_seconds"] == 120.3
        assert row["bounce_rate"] == 0.25

    @patch("run_ga4._run_report")
    def test_rate_limit_retry(self, mock_run_report):
        mock_run_report.side_effect = [None, SAMPLE_PAGE_RESPONSE]

        rows = list(get_page_analytics("prop/123", "email", "key"))
        assert len(rows) == 1


class TestEventAnalytics:
    @patch("run_ga4._run_report")
    def test_event_analytics_success(self, mock_run_report):
        mock_run_report.return_value = SAMPLE_EVENT_RESPONSE

        rows = list(get_event_analytics("prop/123", "email", "key"))
        assert len(rows) == 2

        assert rows[0]["report_date"] == "20260701"
        assert rows[0]["event_name"] == "page_view"
        assert rows[0]["event_count"] == 500
        assert rows[0]["user_count"] == 200

        assert rows[1]["event_name"] == "click"
        assert rows[1]["event_count"] == 50

    @patch("run_ga4._run_report")
    def test_rate_limit_retry(self, mock_run_report):
        mock_run_report.side_effect = [None, SAMPLE_EVENT_RESPONSE]

        rows = list(get_event_analytics("prop/123", "email", "key"))
        assert len(rows) == 2


class TestGa4Source:
    @patch("run_ga4._run_report")
    def test_source_returns_all_resources(self, mock_run_report):
        mock_run_report.side_effect = [
            SAMPLE_DAILY_RESPONSE,
            SAMPLE_PAGE_RESPONSE,
            SAMPLE_EVENT_RESPONSE,
        ]

        source = ga4_source("prop/123", "email", "key")
        resources = list(source.resources.values())
        assert len(resources) == 3
        names = [r.name for r in resources]
        assert "daily_stats" in names
        assert "page_analytics" in names
        assert "event_analytics" in names


class TestParseRows:
    def test_parse_rows_empty(self):
        assert _parse_rows(None) == []
        assert _parse_rows({}) == []
        assert _parse_rows({"rows": []}) == []

    def test_parse_rows_with_data(self):
        data = {
            "dimensionHeaders": [{"name": "date"}],
            "metricHeaders": [{"name": "sessions"}],
            "rows": [
                {
                    "dimensionValues": [{"value": "20260701"}],
                    "metricValues": [{"value": "100"}],
                }
            ],
        }
        rows = _parse_rows(data)
        assert len(rows) == 1
        assert rows[0]["date"] == "20260701"
        assert rows[0]["sessions"] == "100"


class TestRunReportErrorHandling:
    @patch("run_ga4.requests.post")
    def test_429_status_code(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_resp.headers = {"Retry-After": "1"}
        mock_post.return_value = mock_resp

        result = _run_report("prop/123", "token", {})
        assert result is None

    @patch("run_ga4.requests.post")
    def test_401_error_in_body(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}
        mock_post.return_value = mock_resp

        with pytest.raises(Exception, match="Token expired"):
            _run_report("prop/123", "bad-token", {})
