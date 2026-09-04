"""Tests for the pure pipeline plan/status module (spec A1/A3/A4, design D1/D7).

The module under test must not yet exist — this file is written FIRST so the
suite fails (RED) until ``src/agency_analytics/pipeline_plan.py`` lands.
"""

import json

import pytest

from agency_analytics.pipeline_plan import (
    CONNECTOR_MODELS,
    INVESTMENT_MARTS,
    INVESTMENT_NEEDS,
    MONITORING_CHAIN,
    build_plan,
    main,
    run_status,
)

EXPECTED_MONITORING_CHAIN = (
    "stg_public__pipeline_runs",
    "stg_public__pipeline_run_steps",
    "int_pipeline_daily_summary",
    "pipeline_monitoring",
)


# ─── Constants (spec A1) ───────────────────────────────────────────────────


def test_monitoring_chain_constant_is_exact_chain():
    """The monitoring chain must always be selected, parents first."""
    assert MONITORING_CHAIN == EXPECTED_MONITORING_CHAIN
    assert MONITORING_CHAIN[0] == "stg_public__pipeline_runs"
    assert MONITORING_CHAIN[1] == "stg_public__pipeline_run_steps"
    assert MONITORING_CHAIN[-1] == "pipeline_monitoring"


def test_connector_models_have_no_dead_public_case():
    """Monitoring parents come from the chain constant, not connector mapping."""
    assert "public" not in CONNECTOR_MODELS
    assert CONNECTOR_MODELS.keys() == {
        "meta",
        "tiktok",
        "google",
        "facebook",
        "instagram",
        "tiktok_organic",
        "youtube",
        "pinterest",
        "ga4",
        "gtm",
    }


def test_connector_models_are_staging_only():
    """Every mapped model belongs to the staging whitelist — never raw/public/marts."""
    for connector, models in CONNECTOR_MODELS.items():
        assert connector
        assert len(models) >= 1
        assert len(set(models)) == len(models), f"{connector} has duplicate models"
        assert all(model.startswith("stg_") for model in models), connector


def test_investment_constants_match_design():
    assert INVESTMENT_NEEDS == frozenset({"meta", "tiktok", "google"})
    assert INVESTMENT_MARTS == (
        "int_unified_spend",
        "ad_spend_summary",
        "campaign_performance",
    )


# ─── build_plan (spec A1/A4) ───────────────────────────────────────────────


def test_build_plan_meta_tiktok_happy_path_snapshot():
    plan = build_plan(["meta", "tiktok"])
    assert plan.models == (
        "stg_meta__ads",
        "stg_meta__campaigns",
        "stg_tiktok__ads",
        "stg_tiktok__campaigns",
        *EXPECTED_MONITORING_CHAIN,
    )
    assert plan.investment is False


def test_build_plan_zero_connectors_returns_chain_only():
    plan = build_plan([])
    assert plan.models == EXPECTED_MONITORING_CHAIN
    assert plan.investment is False


def test_build_plan_three_model_connector_snapshot():
    plan = build_plan(["facebook"])
    assert plan.models == (
        "stg_facebook__page_posts",
        "stg_facebook__feed",
        "stg_facebook__page_insights_daily",
        *EXPECTED_MONITORING_CHAIN,
    )


def test_build_plan_instagram_includes_totals_staging():
    """IG7-R2/WU4: the instagram plan builds the new totals staging model.

    The connector split (obs #534 WU2) moved window-scoped total_value metrics
    to ``raw_instagram.insights_totals``; the nightly dbt select must construct
    its passthrough ``stg_instagram__insights_totals`` next to the narrowed
    daily model.
    """
    plan = build_plan(["instagram"])
    assert plan.models == (
        "stg_instagram__media",
        "stg_instagram__insights_daily",
        "stg_instagram__insights_totals",
        *EXPECTED_MONITORING_CHAIN,
    )


def test_build_plan_never_drags_models_outside_whitelist():
    plan = build_plan(["meta"])
    whitelist = set(CONNECTOR_MODELS["meta"]) | set(EXPECTED_MONITORING_CHAIN)
    assert set(plan.models) <= whitelist
    assert "stg_tiktok__ads" not in plan.models
    assert "int_unified_spend" not in plan.models
    assert "stg_public__pipeline_runs" in plan.models


def test_build_plan_investment_requires_all_three_ads_connectors():
    assert build_plan(["meta", "tiktok", "google"]).investment is True
    assert build_plan(["meta", "tiktok"]).investment is False
    assert build_plan(["google", "meta", "tiktok"]).investment is True


def test_build_plan_deterministic_and_deduplicates_connectors():
    first = build_plan(["google", "google", "meta"])
    second = build_plan(["google", "meta"])
    assert first.models == second.models
    assert first.models == (
        "stg_google__ads",
        "stg_google__campaigns",
        "stg_meta__ads",
        "stg_meta__campaigns",
        *EXPECTED_MONITORING_CHAIN,
    )


# ─── run_status (spec A3) ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("connectors_ok", "connectors_failed", "dbt_status", "expected"),
    [
        (1, 0, "success", "success"),
        (0, 0, "success", "success"),
        (1, 0, "failed", "failed"),
        (0, 0, "failed", "failed"),
        (2, 1, "success", "failed"),
        (3, 2, "failed", "failed"),
    ],
)
def test_run_status_matrix(connectors_ok, connectors_failed, dbt_status, expected):
    assert run_status(connectors_ok, connectors_failed, dbt_status) == expected


# ─── main() JSON contract consumed by scripts/pipeline.sh (design D1) ─────


def test_main_plan_prints_single_json_line(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["pipeline_plan", "plan", "--connectors", "meta,tiktok"])
    assert main() == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    plan = build_plan(["meta", "tiktok"])
    assert payload == {"models": list(plan.models), "investment": plan.investment}


def test_main_status_prints_single_json_line(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["pipeline_plan", "status", "--ok", "1", "--failed", "0", "--dbt-status", "failed"],
    )
    assert main() == 0
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0]) == {"status": "failed"}
