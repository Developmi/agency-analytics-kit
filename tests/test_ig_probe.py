"""Tests for the read-only Instagram insights probe (spec IG4, design D8).

The module under test (``agency_analytics.ig_probe``) does not exist yet — this
file is written FIRST so the suite fails (RED) until it lands.

The probe core is pure: every test injects a fake ``fetch(url, params) -> dict``
that models the Meta Graph API responses (account node, insights windows,
breakdowns, error payloads). No live API call and no DB write is ever made.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
import yaml

import agency_analytics.ig_probe as ig_probe
from agency_analytics.ig_probe import (
    FOLLOWERS_GATE,
    TOTAL_VALUE_COMMON_METRICS,
    ProbeAPIError,
    probe_core,
)

NOW = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
NOW_TS = int(NOW.timestamp())
DAY = 86400

COMMON_METRIC_VALUES = {
    "views": 2500,
    "likes": 200,
    "comments": 50,
    "shares": 25,
    "saves": 30,
    "total_interactions": 500,
    "accounts_engaged": 100,
    "replies": 5,
    "reposts": 3,
}
GATED_METRIC_VALUES = {"follows_and_unfollows": 7, "profile_links_taps": 12}
BREAKDOWN_DIMS = {
    "media_product_type": {
        metric: ["FEED", "REELS", "STORY"]
        for metric in ("views", "likes", "comments", "shares", "saves", "total_interactions")
    },
    "follow_type": {"follows_and_unfollows": ["FOLLOWER", "NON_FOLLOWER"]},
    "contact_button_type": {"profile_links_taps": ["EMAIL", "CALL", "WEBSITE"]},
}


def _ts(dt: datetime) -> int:
    return int(dt.timestamp())


def _window(since_dt: datetime, until_dt: datetime) -> dict[str, Any]:
    """One values list per calendar day inside [since_dt, until_dt] (series shape)."""
    values = []
    cursor = since_dt
    while cursor <= until_dt:
        values.append({"value": 1, "end_time": cursor.isoformat().replace("+00:00", "+0000")})
        cursor += timedelta(days=1)
    return {"values": values}


def _window_end_offset(params: dict[str, Any]) -> int:
    until = int(params["until"])
    return max(0, round((NOW_TS - until) / DAY))


def api_fetch(
    *,
    followers: int = 5000,
    reach_limit: int | None = None,
    fc_limit: int = 0,
    tv_limit: int | None = None,
    tv_empty_at: frozenset[int] = frozenset(),
    tv_error_at: frozenset[int] = frozenset(),
    absent_metrics: frozenset[str] = frozenset(),
    null_metrics: frozenset[str] = frozenset(),
    tv_value_overrides: dict[str, int] | None = None,
    breakdown_errors: frozenset[tuple[str, str]] = frozenset(),
    account_error: dict[str, Any] | None = None,
    record: list[dict[str, Any]] | None = None,
):
    """Scripted Graph API fake: ok responses unless the test configures a fault.

    Series metrics respond up to their configured window-end offset limit
    (reach unlimited by default; follower_count only at offset 0, the 30-day
    doc ceiling). total_value windows respond unless forced empty or in error.
    """
    values_by_metric = {**COMMON_METRIC_VALUES, **GATED_METRIC_VALUES}
    if tv_value_overrides:
        values_by_metric.update(tv_value_overrides)

    def _series_payload(metric: str, since_ts: int, until_ts: int) -> dict[str, Any]:
        since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
        until_dt = datetime.fromtimestamp(until_ts, tz=timezone.utc)
        return {"data": [{"name": metric, "period": "day", **_window(since_dt, until_dt)}]}

    def _total_payload(names: list[str], with_breakdown: str | None = None) -> dict[str, Any]:
        data = []
        for name in names:
            if name in absent_metrics:
                continue
            entry: dict[str, Any] = {"name": name, "period": "day"}
            if name in null_metrics:
                tv: dict[str, Any] = {"value": None}
            else:
                tv = {"value": values_by_metric.get(name, 0)}
            if with_breakdown and name in BREAKDOWN_DIMS.get(with_breakdown, {}):
                dims = BREAKDOWN_DIMS[with_breakdown][name]
                tv["breakdowns"] = [
                    {
                        "dimension_keys": [with_breakdown],
                        "results": [
                            {"dimension_values": [dim], "value": i + 1}
                            for i, dim in enumerate(dims)
                        ],
                    }
                ]
            entry["total_value"] = tv
            data.append(entry)
        return {"data": data}

    if record is None:
        record = []

    def fetch(url: str, params: dict[str, Any]) -> dict[str, Any]:
        record.append({"url": url, "params": dict(params)})
        if "/insights" not in url:
            if account_error is not None:
                return account_error
            return {"id": "ig_biz_1", "username": "client", "followers_count": followers}

        metric = params["metric"]
        offset = _window_end_offset(params)
        since_ts = int(params["since"])
        until_ts = int(params["until"])
        metric_type = params.get("metric_type")
        breakdown = params.get("breakdown")

        if metric_type == "time_series":
            limit = reach_limit if metric == "reach" else fc_limit
            if limit is not None and offset > limit:
                return {"data": []}
            return _series_payload(metric, since_ts, until_ts)

        if breakdown:
            if (breakdown, metric) in breakdown_errors:
                return {"error": {"code": 100, "message": f"breakdown not supported: {metric}"}}
            return _total_payload([metric], with_breakdown=breakdown)

        if "," in metric:
            # Comma-joined common batch.
            if offset in tv_error_at:
                return {"error": {"code": 100, "message": "invalid parameter batch"}}
            if offset in tv_empty_at or (tv_limit is not None and offset > tv_limit):
                return {"data": []}
            return _total_payload(list(TOTAL_VALUE_COMMON_METRICS))
        # Single gated total_value call.
        return _total_payload([metric])

    fetch.record = record  # type: ignore[attr-defined]
    return fetch


# ─── probe_core: full happy-path report (IG4-R2) ────────────────────────────


def test_probe_core_ok_full_report_shape():
    """A healthy account yields the full IG4-R2 report: account, daily series,
    tv horizon, per-metric totals and the breakdown matrix."""
    fetch = api_fetch()
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["status"] == "ok"
    assert report["read_only"] is True
    assert report["generated_at"] == NOW.isoformat()
    assert report["account"] == {
        "id": "ig_biz_1",
        "username": "client",
        "followers_count": 5000,
        "eligibility": ">=100",
    }

    # Daily series: reach responds over every probed window; follower_count
    # responds only in the current 30-day window (doc ceiling).
    assert report["daily"]["reach"]["responds"] is True
    assert report["daily"]["reach"]["series_shape"] == "time_series"
    assert report["daily"]["follower_count"]["responds"] is True
    assert report["daily"]["follower_count"]["series_shape"] == "time_series"
    assert report["daily"]["follower_count"]["max_history_days"] == 0

    # Horizon: requested 90d, every evidence window responded → confirmed 90.
    horizon = report["horizon"]
    assert horizon["requested_days"] == 90
    assert horizon["confirmed_days"] == 90
    evidence_offsets = [item["offset_days"] for item in horizon["evidence"]]
    assert evidence_offsets == [0, 30, 60, 90]
    assert all(item["responds"] for item in horizon["evidence"])

    # Per-metric totals: every common metric responded in the current window.
    for name, value in COMMON_METRIC_VALUES.items():
        assert report["metrics"][name]["responds"] is True
        assert report["metrics"][name]["metric_type"] == "total_value"
        assert report["metrics"][name]["per_window_value"] == value
        assert report["metrics"][name]["state"] == "ok"
    assert report["metrics"]["follows_and_unfollows"]["state"] == "ok"
    assert report["metrics"]["profile_links_taps"]["state"] == "ok"

    # Breakdown matrix: documented axes report their dimension values.
    bd = report["breakdowns"]
    assert bd["media_product_type"]["supported"]["views"] == ["FEED", "REELS", "STORY"]
    assert bd["media_product_type"]["supported"]["comments"] == ["FEED", "REELS", "STORY"]
    assert bd["follow_type"]["supported"]["follows_and_unfollows"] == ["FOLLOWER", "NON_FOLLOWER"]
    assert bd["contact_button_type"]["supported"]["profile_links_taps"] == [
        "CALL",
        "EMAIL",
        "WEBSITE",
    ]


def test_probe_core_window_params_are_deterministic():
    """Windows are anchored to the injected ``now``: since = now-(offset+30)d,
    until = now-offset, both as Unix timestamps; window_end is the ISO date."""
    fetch = api_fetch(record=[])
    probe_core("ig_biz_1", "tok", fetch, NOW, horizon_days=30)
    calls = fetch.record  # type: ignore[attr-defined]

    tv_calls = [
        c
        for c in calls
        if "total_value" in c["params"].get("metric_type", "")
        and c["params"].get("metric") not in ("reach", "follower_count")
    ]
    assert len(tv_calls) >= 1
    batch = next(c for c in tv_calls if "," in c["params"]["metric"])
    assert batch["params"]["metric"].split(",") == list(TOTAL_VALUE_COMMON_METRICS)
    assert batch["params"]["period"] == "day"
    assert batch["params"]["metric_type"] == "total_value"
    assert batch["params"]["access_token"] == "tok"
    # Offset 0 window: last 30 days ending exactly at `now`.
    assert batch["params"]["since"] == int((NOW - timedelta(days=30)).timestamp())
    assert batch["params"]["until"] == NOW_TS

    # A window ending 30 days ago spans [now-60, now-30).
    offset_30 = next(
        c
        for c in tv_calls
        if int(c["params"]["until"]) == int((NOW - timedelta(days=30)).timestamp())
    )
    assert offset_30["params"]["since"] == int((NOW - timedelta(days=60)).timestamp())
    assert offset_30["url"].endswith("/ig_biz_1/insights")


def test_probe_core_confirmed_days_follows_cliff():
    """When total_value stops responding at the 90-day offset window, the
    confirmed horizon is 60 and that window is recorded as non-responding."""
    fetch = api_fetch(tv_empty_at=frozenset({90}))
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    horizon = report["horizon"]
    assert horizon["confirmed_days"] == 60
    by_offset = {item["offset_days"]: item for item in horizon["evidence"]}
    assert by_offset[90]["responds"] is False
    assert by_offset[60]["responds"] is True
    # Metrics keep their value at the deepest responding window.
    assert report["metrics"]["views"]["max_history_days"] == 60


def test_probe_core_empty_dataset_is_not_zero():
    """IG3-R2: an empty ``data: []`` current window yields None for every
    metric in that window — no fabricated 0 anywhere in the report."""
    fetch = api_fetch(tv_empty_at=frozenset({0}))
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["horizon"]["evidence"][0]["responds"] is False
    for name in COMMON_METRIC_VALUES:
        assert report["metrics"][name]["per_window_value"] is None
        assert report["metrics"][name]["state"] == "empty"
        assert report["metrics"][name]["responds"] is False


def test_probe_core_real_zero_is_preserved():
    """A literal 0 in the API response is a real zero and stays 0 (state ok),
    the exact opposite of the empty-dataset None."""
    fetch = api_fetch(tv_value_overrides={"views": 0})
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["metrics"]["views"]["per_window_value"] == 0
    assert report["metrics"]["views"]["state"] == "ok"
    assert report["metrics"]["views"]["responds"] is True
    # A different metric in the same window keeps its real value.
    assert report["metrics"]["likes"]["per_window_value"] == 200


def test_probe_core_null_value_is_empty_not_zero():
    """A total_value entry that exists but carries ``value: null`` records
    absence (state empty, per-window None) — never a fabricated 0. This is the
    live shape seen for gated follows_and_unfollows without its breakdown."""
    fetch = api_fetch(null_metrics=frozenset({"follows_and_unfollows"}))
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["metrics"]["follows_and_unfollows"]["state"] == "empty"
    assert report["metrics"]["follows_and_unfollows"]["per_window_value"] is None
    assert report["metrics"]["follows_and_unfollows"]["responds"] is False
    assert report["metrics"]["profile_links_taps"]["state"] == "ok"


def test_probe_core_partial_metric_absence_is_empty():
    """A metric omitted from every batch entry is 'empty', never 0 or error."""
    fetch = api_fetch(absent_metrics=frozenset({"reposts"}))
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["metrics"]["reposts"]["state"] == "empty"
    assert report["metrics"]["reposts"]["per_window_value"] is None
    assert report["metrics"]["views"]["state"] == "ok"


def test_probe_core_breakdown_unsupported_recorded_not_fatal():
    """Error 100 on a breakdown call marks the axis/metric unsupported; the
    probe keeps running and reports status ok (breakdown discovery)."""
    fetch = api_fetch(breakdown_errors=frozenset({("media_product_type", "comments")}))
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["status"] == "ok"
    bd = report["breakdowns"]["media_product_type"]
    assert "comments" in bd["unsupported"]
    assert "views" in bd["supported"]
    assert report["metrics"]["comments"]["state"] == "ok"  # base metric unaffected


def test_probe_core_below_100_followers_gates_calls():
    """IG3-R1/d: below the follower gate the gated metrics are skipped and no
    API call for them (nor their breakdown axes) is ever issued."""
    fetch = api_fetch(followers=FOLLOWERS_GATE - 1, record=[])
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    assert report["account"]["eligibility"] == "<100"
    for name in ("follows_and_unfollows", "profile_links_taps"):
        assert report["metrics"][name]["state"] == "gated_skipped"
        assert report["metrics"][name]["responds"] is False
        assert report["metrics"][name]["per_window_value"] is None
    assert report["breakdowns"]["follow_type"]["skipped"] == ["follows_and_unfollows"]
    assert report["breakdowns"]["contact_button_type"]["skipped"] == ["profile_links_taps"]

    gated_metric_calls = [
        c
        for c in fetch.record  # type: ignore[attr-defined]
        if c["params"].get("metric") in ("follows_and_unfollows", "profile_links_taps")
    ]
    assert gated_metric_calls == []
    breakdown_axis_calls = [
        c
        for c in fetch.record  # type: ignore[attr-defined]
        if c["params"].get("breakdown") in ("follow_type", "contact_button_type")
    ]
    assert breakdown_axis_calls == []
    # Ungated media_product_type breakdowns are still probed.
    assert any(c["params"].get("breakdown") == "media_product_type" for c in fetch.record)  # type: ignore[attr-defined]


def test_probe_core_breakdown_skippable():
    """--skip-breakdowns path: no breakdown call is made and the report omits
    the matrix key only when requested through the flag."""
    fetch = api_fetch(record=[])
    probe_core("ig_biz_1", "tok", fetch, NOW, skip_breakdowns=True)
    breakdown_calls = [c for c in fetch.record if c["params"].get("breakdown")]  # type: ignore[attr-defined]
    assert breakdown_calls == []


def test_probe_core_reach_probes_past_tv_horizon():
    """IG4-R2(b): daily reach is probed at a deep offset past the 90-day
    total_value ceiling to confirm the daily series survives the split."""
    fetch = api_fetch(record=[])
    report = probe_core("ig_biz_1", "tok", fetch, NOW)

    reach_calls = [c for c in fetch.record if c["params"].get("metric") == "reach"]  # type: ignore[attr-defined]
    reach_offsets = sorted(
        {max(0, round((NOW_TS - int(c["params"]["until"])) / DAY)) for c in reach_calls}
    )
    assert 0 in reach_offsets and 90 in reach_offsets
    assert max(reach_offsets) >= 200
    assert report["daily"]["reach"]["max_history_days"] >= 200


def test_probe_core_account_api_error_raises():
    """A token/business-id level API error (code 190) aborts with a typed
    exception so the CLI can report a clear non-fatal status."""
    fetch = api_fetch(account_error={"error": {"code": 190, "message": "Token expired"}})
    with pytest.raises(ProbeAPIError) as excinfo:
        probe_core("ig_biz_1", "tok", fetch, NOW)
    assert excinfo.value.code == 190


def test_probe_core_common_batch_error_aborts():
    """Design D4 parity: error 100 on the common/gated batch would corrupt a
    window row, so it aborts the probe instead of being treated as a
    measurement (only breakdown error 100 is discovery)."""
    fetch = api_fetch(tv_error_at=frozenset({0}))
    with pytest.raises(ProbeAPIError) as excinfo:
        probe_core("ig_biz_1", "tok", fetch, NOW)
    assert excinfo.value.code == 100


# ─── CLI (task 1.3): YAML resolution, JSON line, read-only gate ─────────────


def _write_client_yaml(
    tmp_path,
    *,
    active: bool = True,
    enabled: bool = True,
    token_env: str = "IG_TOKEN_TEST",
    business_id: str = "ig_biz_1",
) -> str:
    payload = {
        "client_id": "test_client",
        "active": active,
        "connectors": {
            "instagram": {
                "enabled": enabled,
                "instagram_business_id": business_id,
                "token_env": token_env,
            }
        },
    }
    path = tmp_path / "test_client.yml"
    path.write_text(yaml.safe_dump(payload))
    return str(path)


def _cli_fetch(monkeypatch, record):
    fake = api_fetch(record=record)
    monkeypatch.setattr(ig_probe, "_live_fetch", fake)


def test_main_requires_read_only_flag(capsys, tmp_path, monkeypatch):
    """The explicit read-only acknowledgement is mandatory; without it the
    probe reports a clear cannot-run status and makes zero API calls."""
    _write_client_yaml(tmp_path)
    record: list[dict[str, Any]] = []
    _cli_fetch(monkeypatch, record)
    monkeypatch.setenv("IG_TOKEN_TEST", "tok")

    code = ig_probe.main(
        ["--client", "test_client", "--clients-dir", str(tmp_path), "--business-id", "ig_biz_1"]
    )
    assert code == 3
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["status"] == "error"
    assert "read-only" in payload["error"]["reason"]
    assert record == []


def test_main_resolves_client_yaml_and_prints_single_json_line(capsys, tmp_path, monkeypatch):
    """CLI resolves business id + token env from the client YAML (env CLIENTS_DIR
    pattern like run_instagram), runs the probe and prints one JSON line."""
    _write_client_yaml(tmp_path)
    record: list[dict[str, Any]] = []
    _cli_fetch(monkeypatch, record)
    monkeypatch.setenv("IG_TOKEN_TEST", "tok")

    code = ig_probe.main(["--client", "test_client", "--clients-dir", str(tmp_path), "--read-only"])
    assert code == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["status"] == "ok"
    assert payload["account"]["id"] == "ig_biz_1"
    assert payload["account"]["username"] == "client"
    assert any(c["url"].endswith("/ig_biz_1/insights") for c in record)
    assert payload["read_only"] is True


def test_main_business_id_and_token_env_overrides(capsys, tmp_path, monkeypatch):
    """--business-id and --token-env override the YAML-resolved values."""
    _write_client_yaml(tmp_path, business_id="from_yml", token_env="IG_TOKEN_TEST")
    record: list[dict[str, Any]] = []
    _cli_fetch(monkeypatch, record)
    monkeypatch.setenv("OTHER_IG_TOKEN", "tok")

    code = ig_probe.main(
        [
            "--client",
            "test_client",
            "--clients-dir",
            str(tmp_path),
            "--business-id",
            "ig_biz_2",
            "--token-env",
            "OTHER_IG_TOKEN",
            "--read-only",
        ]
    )
    assert code == 0
    payload = json.loads([line for line in capsys.readouterr().out.splitlines() if line.strip()][0])
    assert payload["account"]["id"] == "ig_biz_2"
    assert payload["status"] == "ok"


def test_main_missing_token_is_non_fatal(capsys, tmp_path, monkeypatch):
    """No token in the environment → documented non-fatal exit 3, clear state."""
    _write_client_yaml(tmp_path, token_env="IG_TOKEN_MISSING")
    monkeypatch.delenv("IG_TOKEN_MISSING", raising=False)

    code = ig_probe.main(["--client", "test_client", "--clients-dir", str(tmp_path), "--read-only"])
    assert code == 3
    payload = json.loads([line for line in capsys.readouterr().out.splitlines() if line.strip()][0])
    assert payload["status"] == "error"
    assert "token" in payload["error"]["reason"].lower()


def test_main_runtime_api_error_is_non_fatal(capsys, tmp_path, monkeypatch):
    """A transport/API abort mid-run is reported as status error with exit 4 —
    the probe never breaks the pipeline (IG4-R3)."""
    _write_client_yaml(tmp_path)
    monkeypatch.setenv("IG_TOKEN_TEST", "tok")
    monkeypatch.setattr(
        ig_probe,
        "_live_fetch",
        lambda url, params: {"error": {"code": 190, "message": "Token expired"}},
    )

    code = ig_probe.main(["--client", "test_client", "--clients-dir", str(tmp_path), "--read-only"])
    assert code == 4
    payload = json.loads([line for line in capsys.readouterr().out.splitlines() if line.strip()][0])
    assert payload["status"] == "error"
    assert payload["error"]["code"] == 190


def test_main_writes_output_file(tmp_path, monkeypatch):
    """--output persists the exact JSON report to a file instead of stdout."""
    _write_client_yaml(tmp_path)
    monkeypatch.setenv("IG_TOKEN_TEST", "tok")
    _cli_fetch(monkeypatch, [])
    out_file = tmp_path / "probe.json"

    code = ig_probe.main(
        [
            "--client",
            "test_client",
            "--clients-dir",
            str(tmp_path),
            "--read-only",
            "--output",
            str(out_file),
        ]
    )
    assert code == 0
    payload = json.loads(out_file.read_text())
    assert payload["status"] == "ok"
    assert payload["read_only"] is True
