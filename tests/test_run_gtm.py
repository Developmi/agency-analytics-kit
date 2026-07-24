import json
from unittest.mock import MagicMock, patch

import pytest
from run_gtm import (
    _headers,
    get_containers,
    get_tags,
    get_triggers,
    gtm_source,
)

SAMPLE_CONTAINERS_RESPONSE = {
    "container": [
        {
            "containerId": "1001",
            "accountId": "123456789",
            "name": "Main Container",
            "publicId": "GTM-ABC123",
            "usageContext": ["web", "amp"],
        },
        {
            "containerId": "1002",
            "accountId": "123456789",
            "name": "iOS Container",
            "publicId": "GTM-DEF456",
            "usageContext": ["ios"],
        },
    ]
}

SAMPLE_TAGS_RESPONSE = {
    "tag": [
        {
            "tagId": "t1",
            "type": "html",
            "name": "Google Analytics Tag",
            "firingTriggerId": ["tr1", "tr2"],
            "blockingTriggerId": ["tr3"],
            "workspaceId": "w1",
        }
    ]
}

SAMPLE_TRIGGERS_RESPONSE = {
    "trigger": [
        {
            "triggerId": "tr1",
            "type": "pageview",
            "name": "All Pages",
            "filter": [
                {
                    "type": "contains",
                    "parameter": [
                        {"type": "template", "key": "arg0", "value": "{{Page URL}}"},
                        {"type": "template", "key": "arg1", "value": "/products"},
                    ],
                }
            ],
        }
    ]
}


class TestHeaders:
    def test_headers_format(self):
        h = _headers("test-token")
        assert h["Authorization"] == "Bearer test-token"


class TestContainers:
    @patch("run_gtm.requests.get")
    def test_containers_success(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE
        mock_get.return_value = mock_resp

        rows = list(get_containers("accounts/123456789", "token"))
        assert len(rows) == 2

        c1 = rows[0]
        assert c1["container_id"] == "1001"
        assert c1["account_id"] == "123456789"
        assert c1["name"] == "Main Container"
        assert c1["public_id"] == "GTM-ABC123"
        assert json.loads(c1["usage_context"]) == ["web", "amp"]

        c2 = rows[1]
        assert c2["container_id"] == "1002"
        assert c2["public_id"] == "GTM-DEF456"

    @patch("run_gtm.requests.get")
    def test_rate_limit_retry(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"Retry-After": "1"}

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        mock_get.side_effect = [rate_resp, ok_resp]

        rows = list(get_containers("accounts/123456789", "token"))
        assert len(rows) == 2

    @patch("run_gtm.requests.get")
    def test_token_expired(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}
        mock_get.return_value = mock_resp

        with pytest.raises(Exception, match="Token expired"):
            list(get_containers("accounts/123456789", "bad-token"))


class TestTags:
    @patch("run_gtm.requests.get")
    def test_tags_success(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = SAMPLE_TAGS_RESPONSE

        mock_get.side_effect = [containers_resp, tags_resp, tags_resp]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2

        tag = rows[0]
        assert tag["tag_id"] == "t1"
        assert tag["container_id"] == "1001"
        assert tag["type"] == "html"
        assert tag["name"] == "Google Analytics Tag"
        assert json.loads(tag["firing_triggers"]) == ["tr1", "tr2"]
        assert json.loads(tag["blocking_triggers"]) == ["tr3"]
        assert "tagmanager.google.com" in tag["tag_manager_url"]

    @patch("run_gtm.requests.get")
    def test_multiple_containers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        tags_resp_1 = MagicMock()
        tags_resp_1.status_code = 200
        tags_resp_1.json.return_value = {
            "tag": [
                {
                    "tagId": "t1",
                    "type": "html",
                    "name": "Tag in Container 1",
                    "firingTriggerId": [],
                    "blockingTriggerId": [],
                    "workspaceId": "w1",
                }
            ]
        }

        tags_resp_2 = MagicMock()
        tags_resp_2.status_code = 200
        tags_resp_2.json.return_value = {
            "tag": [
                {
                    "tagId": "t2",
                    "type": "gtag",
                    "name": "Tag in Container 2",
                    "firingTriggerId": [],
                    "blockingTriggerId": [],
                    "workspaceId": "w2",
                }
            ]
        }

        mock_get.side_effect = [containers_resp, tags_resp_1, tags_resp_2]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2
        assert rows[0]["container_id"] == "1001"
        assert rows[1]["container_id"] == "1002"

    @patch("run_gtm.requests.get")
    def test_rate_limit_on_containers(self, mock_get):
        rate_resp = MagicMock()
        rate_resp.status_code = 429
        rate_resp.headers = {"Retry-After": "1"}

        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        tags_resp = MagicMock()
        tags_resp.status_code = 200
        tags_resp.json.return_value = SAMPLE_TAGS_RESPONSE

        mock_get.side_effect = [rate_resp, containers_resp, tags_resp, tags_resp]

        rows = list(get_tags("accounts/123456789", "token"))
        assert len(rows) == 2


class TestTriggers:
    @patch("run_gtm.requests.get")
    def test_triggers_success(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        triggers_resp = MagicMock()
        triggers_resp.status_code = 200
        triggers_resp.json.return_value = SAMPLE_TRIGGERS_RESPONSE

        mock_get.side_effect = [containers_resp, triggers_resp, triggers_resp]

        rows = list(get_triggers("accounts/123456789", "token"))
        assert len(rows) == 2

        tr = rows[0]
        assert tr["trigger_id"] == "tr1"
        assert tr["container_id"] == "1001"
        assert tr["type"] == "pageview"
        assert tr["name"] == "All Pages"

        filters = json.loads(tr["filter_json"])
        assert len(filters) == 1
        assert filters[0]["type"] == "contains"

    @patch("run_gtm.requests.get")
    def test_empty_triggers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        triggers_resp = MagicMock()
        triggers_resp.status_code = 200
        triggers_resp.json.return_value = {"trigger": []}

        mock_get.side_effect = [containers_resp, triggers_resp, triggers_resp]

        rows = list(get_triggers("accounts/123456789", "token"))
        assert len(rows) == 0

    @patch("run_gtm.requests.get")
    def test_token_expired_on_triggers(self, mock_get):
        containers_resp = MagicMock()
        containers_resp.status_code = 200
        containers_resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE

        error_resp = MagicMock()
        error_resp.status_code = 200
        error_resp.json.return_value = {"error": {"code": 401, "message": "Token expired"}}

        mock_get.side_effect = [containers_resp, error_resp]

        with pytest.raises(Exception, match="Token expired"):
            list(get_triggers("accounts/123456789", "bad-token"))


class TestGtmSource:
    @patch("run_gtm.requests.get")
    def test_source_returns_all_resources(self, mock_get):
        def mock_responses(*args, **kwargs):
            url = args[0] if args else ""
            resp = MagicMock()
            resp.status_code = 200

            if "/tags" in url:
                resp.json.return_value = SAMPLE_TAGS_RESPONSE
            elif "/triggers" in url:
                resp.json.return_value = SAMPLE_TRIGGERS_RESPONSE
            else:
                resp.json.return_value = SAMPLE_CONTAINERS_RESPONSE
            return resp

        mock_get.side_effect = mock_responses

        source = gtm_source("accounts/123456789", "token")
        resources = list(source.resources.values())
        assert len(resources) == 3
        names = [r.name for r in resources]
        assert "containers" in names
        assert "tags" in names
        assert "triggers" in names
