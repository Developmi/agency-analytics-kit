import copy
from unittest.mock import MagicMock, patch

import pytest

GOOGLE_AD_ROW = {
    "adGroupAd": {
        "ad": {"id": "4928103746", "name": "Google Ad Test"},
        "status": "ENABLED",
    },
    "adGroup": {"id": "8573629104"},
    "campaign": {"id": "1298475620"},
    "metrics": {
        "costMicros": 2500000,
        "impressions": 45000,
        "clicks": 2250,
        "ctr": 5.0,
        "averageCpc": 1111,
        "conversions": 15.5,
        "costPerConversion": 161290,
    },
    "segments": {"date": "2024-06-15"},
}

GOOGLE_CAMPAIGN_ROW = {
    "campaign": {
        "id": "1298475620",
        "name": "Google Campaign Test",
        "status": "ENABLED",
        "advertisingChannelType": "SEARCH",
        "budget": {"amountMicros": 50000000, "type": "DAILY"},
        "startDate": "2024-06-01",
        "endDate": "2024-06-30",
    },
    "metrics": {
        "costMicros": 2500000,
        "impressions": 45000,
        "clicks": 2250,
        "ctr": 5.0,
        "averageCpc": 1111,
        "conversions": 15.5,
        "costPerConversion": 161290,
    },
}


def _mock_search_response(results, next_page_token=None):
    resp = MagicMock()
    body = {"results": results}
    if next_page_token:
        body["nextPageToken"] = next_page_token
    resp.json.return_value = body
    return resp


def _mock_error_response(code, message, **extra):
    resp = MagicMock()
    error = {"code": code, "message": message}
    if extra:
        error.update(extra)
    resp.json.return_value = {"error": error}
    return resp


def _mock_429_response(retry_delay="5s"):
    resp = MagicMock()
    resp.json.return_value = {
        "error": {
            "code": 429,
            "message": "Rate limit exceeded",
            "details": [{"retryDelay": retry_delay}],
        }
    }
    return resp


class TestGoogleAds:
    def test_ads_success(self, mock_google_api):
        mock_google_api.return_value = _mock_search_response([GOOGLE_AD_ROW])
        from run_google import get_ads

        results = list(get_ads("123-456-7890", "dev_token", "oauth_token"))
        assert len(results) == 1
        r = results[0]
        assert r["ad_id"] == "4928103746"
        assert r["ad_group_id"] == "8573629104"
        assert r["campaign_id"] == "1298475620"
        assert r["ad_name"] == "Google Ad Test"
        assert r["status"] == "ENABLED"
        assert r["spend_usd"] == 2.50
        assert r["impressions"] == 45000
        assert r["clicks"] == 2250
        assert r["ctr"] == 5.0
        assert r["average_cpc"] == 0.001111
        assert r["conversions"] == 15.5
        assert r["cost_per_conversion"] == 0.16129
        assert r["date"] == "2024-06-15"

    def test_campaigns_success(self, mock_google_api):
        mock_google_api.return_value = _mock_search_response([GOOGLE_CAMPAIGN_ROW])
        from run_google import get_campaigns

        results = list(get_campaigns("123-456-7890", "dev_token", "oauth_token"))
        assert len(results) == 1
        r = results[0]
        assert r["campaign_id"] == "1298475620"
        assert r["campaign_name"] == "Google Campaign Test"
        assert r["status"] == "ENABLED"
        assert r["advertising_channel_type"] == "SEARCH"
        assert r["budget"] == 50.00
        assert r["budget_type"] == "DAILY"
        assert r["spend_usd"] == 2.50
        assert r["impressions"] == 45000
        assert r["clicks"] == 2250
        assert r["ctr"] == 5.0
        assert r["average_cpc"] == 0.001111
        assert r["conversions"] == 15.5
        assert r["cost_per_conversion"] == 0.16129
        assert r["start_date"] == "2024-06-01"
        assert r["end_date"] == "2024-06-30"
        assert r["date"] == "2024-06-01"

    def test_rate_limit_retry(self, mock_google_api, mock_sleep):
        error = _mock_429_response(retry_delay="1s")
        success = _mock_search_response([GOOGLE_AD_ROW])
        mock_google_api.side_effect = [error, error, error, error, success]
        from run_google import get_ads

        results = list(get_ads("123-456-7890", "dev_token", "oauth_token"))
        assert len(results) == 1
        assert mock_google_api.call_count == 5

    def test_token_expired(self, mock_google_api):
        mock_google_api.return_value = _mock_error_response(401, "Unauthorized")
        from run_google import get_ads

        with pytest.raises(Exception, match="Token expired"):
            list(get_ads("123-456-7890", "dev_token", "oauth_token"))

    def test_pagination(self, mock_google_api):
        row1 = copy.deepcopy(GOOGLE_AD_ROW)
        row1["adGroupAd"]["ad"]["id"] = "1"
        row2 = copy.deepcopy(GOOGLE_AD_ROW)
        row2["adGroupAd"]["ad"]["id"] = "2"
        page1 = _mock_search_response([row1], next_page_token="abc123")
        page2 = _mock_search_response([row2])
        mock_google_api.side_effect = [page1, page2]
        from run_google import get_ads

        results = list(get_ads("123-456-7890", "dev_token", "oauth_token"))
        assert len(results) == 2
        assert results[0]["ad_id"] == "1"
        assert results[1]["ad_id"] == "2"

    def test_client_not_found(self):
        from run_google import main

        with patch("sys.argv", ["run_google.py", "--client", "nonexistent"]):
            with patch("os.path.exists", return_value=False):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1

    def test_developer_token_error(self, mock_google_api):
        mock_google_api.return_value = _mock_error_response(
            403,
            "Access denied",
            details=[{"errors": [{"reason": "developer-token-does-not-belong-to-this-account"}]}],
        )
        from run_google import get_ads

        with pytest.raises(Exception, match="Invalid developer token"):
            list(get_ads("123-456-7890", "dev_token", "oauth_token"))
