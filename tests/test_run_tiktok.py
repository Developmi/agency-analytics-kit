from unittest.mock import MagicMock, patch

import pytest

TIKTOK_AD_DATA = {
    "ad_id": "1746358291",
    "ad_name": "TikTok Ad Test",
    "ad_status": "DELIVERY_OK",
    "stat_cost": "89.50",
    "stat_impression": 22000,
    "stat_click": 1100,
    "stat_ctr": "5.00",
    "stat_cpc": "0.08",
    "stat_cpm": "4.07",
    "stat_reach": 18000,
    "stat_datetime": "2024-06-15",
}

TIKTOK_CAMPAIGN_DATA = {
    "campaign_id": "2847561930",
    "campaign_name": "TikTok Campaign Test",
    "campaign_status": "ACTIVE",
    "objective": "TRAFFIC",
    "budget": 500000,
    "budget_mode": "DAILY",
    "cost": "300.00",
    "impressions": 50000,
    "clicks": 2500,
    "reach": 40000,
    "start_time": "2024-06-01",
    "end_time": "2024-06-30",
    "create_time": "2024-05-25",
}


def _mock_success_response(list_data, total_page=1, code=0, message="OK"):
    resp = MagicMock()
    resp.json.return_value = {
        "code": code,
        "message": message,
        "data": {
            "list": list_data,
            "page_info": {"total_page": total_page, "page": 1, "page_size": 100},
        },
    }
    return resp


def _mock_error_response(code, message):
    resp = MagicMock()
    resp.json.return_value = {"code": code, "message": message}
    return resp


class TestTikTokAds:
    def test_ads_success(self, mock_tiktok_api):
        mock_tiktok_api.return_value = _mock_success_response([TIKTOK_AD_DATA])
        from run_tiktok import get_ads

        results = list(get_ads("987654321", "fake_token"))
        assert len(results) == 1
        r = results[0]
        assert r["ad_id"] == "1746358291"
        assert r["ad_name"] == "TikTok Ad Test"
        assert r["status"] == "DELIVERY_OK"
        assert r["spend"] == 89.50
        assert r["impressions"] == 22000
        assert r["clicks"] == 1100
        assert r["ctr"] == 5.00
        assert r["cpc"] == 0.08
        assert r["cpm"] == 4.07
        assert r["reach"] == 18000
        assert r["date"] == "2024-06-15"

    def test_campaigns_success(self, mock_tiktok_api):
        mock_tiktok_api.return_value = _mock_success_response([TIKTOK_CAMPAIGN_DATA])
        from run_tiktok import get_campaigns

        results = list(get_campaigns("987654321", "fake_token"))
        assert len(results) == 1
        r = results[0]
        assert r["campaign_id"] == "2847561930"
        assert r["campaign_name"] == "TikTok Campaign Test"
        assert r["status"] == "ACTIVE"
        assert r["objective"] == "TRAFFIC"
        assert r["budget"] == 500000.0
        assert r["budget_type"] == "DAILY"
        assert r["spend"] == 300.00
        assert r["impressions"] == 50000
        assert r["clicks"] == 2500
        assert r["reach"] == 40000
        assert r["start_date"] == "2024-06-01"
        assert r["end_date"] == "2024-06-30"
        assert r["date"] == "2024-05-25"

    def test_rate_limit_retry(self, mock_tiktok_api, mock_sleep):
        error = _mock_error_response(40004, "Request limit reached")
        success = _mock_success_response([TIKTOK_AD_DATA])
        mock_tiktok_api.side_effect = [error, error, error, error, success]
        from run_tiktok import get_ads

        results = list(get_ads("987654321", "fake_token"))
        assert len(results) == 1
        assert mock_tiktok_api.call_count == 5

    def test_token_expired(self, mock_tiktok_api):
        mock_tiktok_api.return_value = _mock_error_response(40007, "Token invalid or expired")
        from run_tiktok import get_ads

        with pytest.raises(Exception, match="Token expired"):
            list(get_ads("987654321", "fake_token"))

    def test_pagination(self, mock_tiktok_api):
        ad1 = {**TIKTOK_AD_DATA, "ad_id": "1"}
        ad2 = {**TIKTOK_AD_DATA, "ad_id": "2"}
        page1 = _mock_success_response([ad1], total_page=2)
        page2 = _mock_success_response([ad2])
        mock_tiktok_api.side_effect = [page1, page2]
        from run_tiktok import get_ads

        results = list(get_ads("987654321", "fake_token"))
        assert len(results) == 2
        assert results[0]["ad_id"] == "1"
        assert results[1]["ad_id"] == "2"

    def test_client_not_found(self):
        from run_tiktok import main

        with patch("sys.argv", ["run_tiktok.py", "--client", "nonexistent"]):
            with patch("os.path.exists", return_value=False):
                with pytest.raises(SystemExit) as exc:
                    main()
        assert exc.value.code == 1

    def test_lifetime_budget_type(self, mock_tiktok_api):
        c = dict(TIKTOK_CAMPAIGN_DATA)
        c["budget_mode"] = "TOTAL"
        mock_tiktok_api.return_value = _mock_success_response([c])
        from run_tiktok import get_campaigns

        results = list(get_campaigns("987654321", "fake_token"))
        assert results[0]["budget_type"] == "LIFETIME"
