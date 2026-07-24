from unittest.mock import MagicMock, patch

import pytest

META_INSIGHTS = {
    "spend": "150.75",
    "impressions": "15000",
    "clicks": "750",
    "reach": "12000",
    "frequency": "1.25",
    "cpm": "10.05",
    "cpc": "0.20",
    "date_start": "2024-06-15",
}

META_AD_DATA = {
    "id": "238476238746",
    "name": "Meta Ad Test",
    "status": "ACTIVE",
    "insights": {"data": [META_INSIGHTS]},
}

META_CAMPAIGN_INSIGHTS = {
    "spend": "5000.00",
    "impressions": "100000",
    "clicks": "5000",
    "reach": "80000",
    "frequency": "1.25",
    "cpm": "50.00",
    "cpc": "1.00",
    "date_start": "2024-06-01",
}

META_CAMPAIGN_DATA = {
    "id": "987654321",
    "name": "Meta Campaign Test",
    "status": "ACTIVE",
    "objective": "OUTCOME_TRAFFIC",
    "daily_budget": "1000000",
    "lifetime_budget": None,
    "start_time": "2024-06-01T00:00:00+0000",
    "stop_time": "2024-06-30T00:00:00+0000",
    "insights": {"data": [META_CAMPAIGN_INSIGHTS]},
}


def _mock_json_response(data, next_page=None):
    resp = MagicMock()
    body = {"data": data}
    if next_page:
        body["paging"] = {"next": next_page}
    else:
        body["paging"] = {}
    resp.json.return_value = body
    return resp


def _mock_error_response(code, message, **extra):
    resp = MagicMock()
    error = {"code": code, "message": message}
    error.update(extra)
    resp.json.return_value = {"error": error}
    return resp


class TestMetaAds:
    def test_ads_success(self, mock_meta_api):
        mock_meta_api.return_value = _mock_json_response([META_AD_DATA])
        from run_meta import get_ads

        results = list(get_ads("123", "fake_token"))
        assert len(results) == 1
        r = results[0]
        assert r["ad_id"] == "238476238746"
        assert r["ad_name"] == "Meta Ad Test"
        assert r["status"] == "ACTIVE"
        assert r["spend"] == 150.75
        assert r["impressions"] == 15000
        assert r["clicks"] == 750
        assert r["reach"] == 12000
        assert r["frequency"] == 1.25
        assert r["cpm"] == 10.05
        assert r["cpc"] == 0.20
        assert r["date"] == "2024-06-15"

    def test_campaigns_success(self, mock_meta_api):
        mock_meta_api.return_value = _mock_json_response([META_CAMPAIGN_DATA])
        from run_meta import get_campaigns

        results = list(get_campaigns("123", "fake_token"))
        assert len(results) == 1
        r = results[0]
        assert r["campaign_id"] == "987654321"
        assert r["campaign_name"] == "Meta Campaign Test"
        assert r["status"] == "ACTIVE"
        assert r["objective"] == "OUTCOME_TRAFFIC"
        assert r["spend"] == 5000.00
        assert r["impressions"] == 100000
        assert r["clicks"] == 5000
        assert r["reach"] == 80000
        assert r["frequency"] == 1.25
        assert r["cpm"] == 50.00
        assert r["cpc"] == 1.00
        assert r["budget"] == 10000.00
        assert r["budget_type"] == "DAILY"
        assert r["start_date"] == "2024-06-01"
        assert r["end_date"] == "2024-06-30"

    def test_rate_limit_retry(self, mock_meta_api, mock_sleep):
        error = _mock_error_response(4, "Rate limit hit", error_subcode=1)
        success = _mock_json_response([META_AD_DATA])
        mock_meta_api.side_effect = [error, error, error, error, success]
        from run_meta import get_ads

        results = list(get_ads("123", "fake_token"))
        assert len(results) == 1
        assert mock_meta_api.call_count == 5

    def test_token_expired(self, mock_meta_api):
        mock_meta_api.return_value = _mock_error_response(190, "Token has expired")
        from run_meta import get_ads

        with pytest.raises(Exception, match="Token expired"):
            list(get_ads("123", "fake_token"))

    def test_pagination(self, mock_meta_api):
        ad1 = {**META_AD_DATA, "id": "1"}
        ad2 = {**META_AD_DATA, "id": "2"}
        page1 = _mock_json_response([ad1], next_page="https://graph.facebook.com/next")
        page2 = _mock_json_response([ad2])
        mock_meta_api.side_effect = [page1, page2]
        from run_meta import get_ads

        results = list(get_ads("123", "fake_token"))
        assert len(results) == 2
        assert results[0]["ad_id"] == "1"
        assert results[1]["ad_id"] == "2"

    def test_client_not_found(self):
        from run_meta import main

        with patch("sys.argv", ["run_meta.py", "--client", "nonexistent"]):
            with patch("os.path.exists", return_value=False):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1

    def test_lifetime_budget_type(self, mock_meta_api):
        c = dict(META_CAMPAIGN_DATA)
        c["daily_budget"] = None
        c["lifetime_budget"] = "50000000"
        mock_meta_api.return_value = _mock_json_response([c])
        from run_meta import get_campaigns

        results = list(get_campaigns("123", "fake_token"))
        assert results[0]["budget"] == 500000.00
        assert results[0]["budget_type"] == "LIFETIME"
