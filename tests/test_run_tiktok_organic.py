import os
import time
from argparse import Namespace
from unittest.mock import MagicMock

import pytest
import yaml

MODULE = "run_tiktok_organic"


def _mock_response(data, status_code=200):
    m = MagicMock()
    m.json.return_value = data
    m.status_code = status_code
    return m


def _ok_response(data: dict) -> dict:
    """Envolver respuesta con el bloque error: ok que TikTok siempre incluye."""
    return {**data, "error": {"code": "ok", "message": ""}}


def test_profile_stats(monkeypatch):
    stats_response = _ok_response({
        "data": {
            "user": {
                "follower_count": 10000,
                "following_count": 500,
                "likes_count": 50000,
                "video_count": 200,
            }
        }
    })

    def mock_request(method, url, **kwargs):
        assert method == "GET"
        assert "Authorization" in kwargs["headers"]
        assert "Bearer" in kwargs["headers"]["Authorization"]
        assert "fields" in kwargs.get("params", {})
        return _mock_response(stats_response)

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)

    mock_state = {
        "tiktok_organic_tokens": {
            "access_token": "already_valid",
            "expires_at": time.time() + 3600,
        }
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "ck", "cs", "rt")
    rows = list(source.resources["profile_stats"])

    assert len(rows) == 1
    assert rows[0]["follower_count"] == 10000
    assert rows[0]["following_count"] == 500
    assert rows[0]["likes_count"] == 50000
    assert rows[0]["video_count"] == 200
    assert rows[0]["report_date"] is not None


def test_videos_organic(monkeypatch):
    videos_response = _ok_response({
        "data": {
            "videos": [
                {
                    "id": "video_1",
                    "title": "My first video",
                    "create_time": 1700000000,
                    "like_count": 150,
                    "comment_count": 20,
                    "share_count": 10,
                    "view_count": 5000,
                }
            ],
            "cursor": 0,
            "has_more": False,
        }
    })

    def mock_request(method, url, **kwargs):
        assert method == "POST"
        assert "Authorization" in kwargs["headers"]
        assert "Bearer" in kwargs["headers"]["Authorization"]
        assert "Content-Type" in kwargs["headers"]
        assert kwargs["headers"]["Content-Type"] == "application/json"
        assert kwargs.get("json", {}).get("max_count") == 20
        return _mock_response(videos_response)

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)

    mock_state = {
        "tiktok_organic_tokens": {
            "access_token": "already_valid",
            "expires_at": time.time() + 3600,
        }
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "ck", "cs", "rt")
    items = list(source.resources["videos_organic"])

    assert len(items) == 1
    assert items[0]["video_id"] == "video_1"
    assert items[0]["title"] == "My first video"
    assert items[0]["like_count"] == 150
    assert items[0]["comment_count"] == 20
    assert items[0]["share_count"] == 10
    assert items[0]["view_count"] == 5000
    assert items[0]["report_date"] is not None


def test_videos_pagination(monkeypatch):
    call_count = 0

    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response(
                _ok_response({
                    "data": {
                        "videos": [
                            {
                                "id": "v1",
                                "title": "V1",
                                "create_time": 1700000001,
                                "like_count": 1,
                                "comment_count": 0,
                                "share_count": 0,
                                "view_count": 10,
                            },
                            {
                                "id": "v2",
                                "title": "V2",
                                "create_time": 1700000002,
                                "like_count": 2,
                                "comment_count": 0,
                                "share_count": 0,
                                "view_count": 20,
                            },
                        ],
                        "cursor": "next_cursor",
                        "has_more": True,
                    }
                })
            )
        return _mock_response(
            _ok_response({
                "data": {
                    "videos": [
                        {
                            "id": "v3",
                            "title": "V3",
                            "like_count": 3,
                            "comment_count": 0,
                            "share_count": 0,
                            "view_count": 30,
                        },
                    ],
                    "cursor": "",
                    "has_more": False,
                }
            })
        )

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)

    mock_state = {
        "tiktok_organic_tokens": {
            "access_token": "already_valid",
            "expires_at": time.time() + 3600,
        }
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "ck", "cs", "rt")
    items = list(source.resources["videos_organic"])

    assert len(items) == 3
    assert call_count == 2


def test_token_auto_refresh(monkeypatch):
    refresh_response = {
        "access_token": "fresh_access_token",
        "refresh_token": "new_refresh_token",
        "expires_in": 86400,
    }

    stats_response = _ok_response({
        "data": {
            "user": {
                "follower_count": 100,
                "following_count": 10,
                "likes_count": 500,
                "video_count": 5,
            }
        }
    })

    refresh_called = False

    def mock_refresh(ck, cs, rt):
        nonlocal refresh_called
        refresh_called = True
        assert ck == "test_ck"
        assert cs == "test_cs"
        assert rt == "test_rt"
        return refresh_response

    monkeypatch.setattr(f"{MODULE}._refresh_access_token", mock_refresh)

    mock_state: dict = {}
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    def mock_request(method, url, **kwargs):
        assert "Bearer fresh_access_token" in kwargs["headers"]["Authorization"]
        return _mock_response(stats_response)

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "test_ck", "test_cs", "test_rt")
    rows = list(source.resources["profile_stats"])

    assert refresh_called
    assert len(rows) == 1
    assert mock_state["tiktok_organic_tokens"]["access_token"] == "fresh_access_token"
    assert mock_state.get("tiktok_organic_refresh_token") == "new_refresh_token"


def test_token_auto_refresh_uses_stored_refresh_token(monkeypatch):
    refresh_response = {
        "access_token": "fresh_token",
        "refresh_token": "rotated_refresh_token",
        "expires_in": 86400,
    }

    stats_response = _ok_response({
        "data": {
            "user": {
                "follower_count": 1,
                "following_count": 1,
                "likes_count": 1,
                "video_count": 1,
            }
        }
    })

    used_refresh = []

    def mock_refresh(ck, cs, rt):
        used_refresh.append(rt)
        return refresh_response

    monkeypatch.setattr(f"{MODULE}._refresh_access_token", mock_refresh)

    mock_state = {
        "tiktok_organic_tokens": {},
        "tiktok_organic_refresh_token": "prev_stored_refresh",
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    def _mock_req(method, url, **kw):
        return _mock_response(stats_response)

    monkeypatch.setattr(f"{MODULE}.requests.request", _mock_req)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("oid", "ck", "cs", "initial_rt")
    list(source.resources["profile_stats"])

    assert used_refresh[0] == "prev_stored_refresh"
    assert mock_state["tiktok_organic_refresh_token"] == "rotated_refresh_token"


def test_refresh_failure(monkeypatch):
    mock_state: dict = {}
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    def raise_error(ck, cs, rt):
        raise Exception("Token refresh failed: Connection refused")

    monkeypatch.setattr(f"{MODULE}._refresh_access_token", raise_error)

    from run_tiktok_organic import tiktok_organic_source

    with pytest.raises(Exception, match="Token refresh failed"):
        tiktok_organic_source("test_open_id", "ck", "cs", "rt")


def test_client_config_loading(monkeypatch, tmp_path):
    config = {
        "active": True,
        "connectors": {
            "tiktok_organic": {
                "enabled": True,
                "open_id": "test_open_789",
                "client_key_env": "TIKTOK_CLIENT_KEY",
                "client_secret_env": "TIKTOK_CLIENT_SECRET",
                "refresh_token_env": "TIKTOK_REFRESH_TOKEN",
            }
        },
    }

    client_dir = tmp_path / "clients"
    client_dir.mkdir()
    client_file = client_dir / "test_client.yml"
    with open(client_file, "w") as f:
        yaml.dump(config, f)

    monkeypatch.setattr(
        f"{MODULE}.argparse.ArgumentParser.parse_args",
        lambda self: Namespace(client="test_client"),
    )
    monkeypatch.setitem(os.environ, "CLIENTS_DIR", str(client_dir))
    monkeypatch.setitem(os.environ, "TIKTOK_CLIENT_KEY", "test_client_key")
    monkeypatch.setitem(os.environ, "TIKTOK_CLIENT_SECRET", "test_client_secret")
    monkeypatch.setitem(os.environ, "TIKTOK_REFRESH_TOKEN", "test_refresh_token")
    monkeypatch.setattr(
        f"{MODULE}.dlt.current.source_state",
        lambda: {
            "tiktok_organic_tokens": {
                "access_token": "already_valid",
                "expires_at": time.time() + 3600,
            }
        },
    )
    monkeypatch.setattr(f"{MODULE}.dlt.pipeline", lambda **kw: MagicMock())

    from run_tiktok_organic import main

    main()


def test_rate_limit_backoff(monkeypatch):
    call_count = 0

    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _mock_response({"error": {"code": 40004, "message": "Rate limit"}})
        return _mock_response(
            _ok_response({
                "data": {
                    "user": {
                        "follower_count": 100,
                        "following_count": 10,
                        "likes_count": 500,
                        "video_count": 5,
                    }
                }
            })
        )

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    mock_state = {
        "tiktok_organic_tokens": {
            "access_token": "already_valid",
            "expires_at": time.time() + 3600,
        }
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "ck", "cs", "rt")
    rows = list(source.resources["profile_stats"])

    assert len(rows) == 1
    assert call_count == 2


def test_request_error_retry(monkeypatch):
    call_count = 0

    def mock_request(method, url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            from dlt.sources.helpers import requests as r

            raise r.RequestException("Connection reset")
        return _mock_response(
            _ok_response({
                "data": {
                    "user": {
                        "follower_count": 100,
                        "following_count": 10,
                        "likes_count": 500,
                        "video_count": 5,
                    }
                }
            })
        )

    monkeypatch.setattr(f"{MODULE}.requests.request", mock_request)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    mock_state = {
        "tiktok_organic_tokens": {
            "access_token": "already_valid",
            "expires_at": time.time() + 3600,
        }
    }
    monkeypatch.setattr(f"{MODULE}.dlt.current.source_state", lambda: mock_state)

    from run_tiktok_organic import tiktok_organic_source

    source = tiktok_organic_source("test_open_id", "ck", "cs", "rt")
    rows = list(source.resources["profile_stats"])

    assert len(rows) == 1
    assert call_count == 2


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    tt_cfg = template["connectors"]["tiktok_organic"]
    assert "open_id" in tt_cfg
    assert "client_key_env" in tt_cfg
    assert "client_secret_env" in tt_cfg
    assert "refresh_token_env" in tt_cfg
