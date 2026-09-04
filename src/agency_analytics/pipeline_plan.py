"""Pure plan/status logic for the nightly pipeline (spec A1-A4, design D1).

This module performs no I/O on purpose: it decides WHICH dbt models a client
run must select and WHETHER a run finished successfully, so the decision can be
unit-tested table-driven (spec A4).

scripts/pipeline.sh invokes it inside the worker container with the bind-mounted
source tree first on ``sys.path`` (no image rebuild required):

    docker exec -w /app/src agency_pipeline python -m agency_analytics.pipeline_plan \
        plan --connectors meta,tiktok
    docker exec -w /app/src agency_pipeline python -m agency_analytics.pipeline_plan \
        status --ok 1 --failed 0 --dbt-status success

Each subcommand prints exactly one JSON line.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Iterable

# Monitoring chain always selected for every client run (spec A1). The
# stg_public__ parents live ONLY here — not in the per-connector mapping.
MONITORING_CHAIN: tuple[str, ...] = (
    "stg_public__pipeline_runs",
    "stg_public__pipeline_run_steps",
    "int_pipeline_daily_summary",
    "pipeline_monitoring",
)

# Connector name -> dbt staging models. The dead ``public`` case is gone.
CONNECTOR_MODELS: dict[str, tuple[str, ...]] = {
    "meta": ("stg_meta__ads", "stg_meta__campaigns"),
    "tiktok": ("stg_tiktok__ads", "stg_tiktok__campaigns"),
    "google": ("stg_google__ads", "stg_google__campaigns"),
    "facebook": (
        "stg_facebook__page_posts",
        "stg_facebook__feed",
        "stg_facebook__page_insights_daily",
    ),
    "instagram": (
        "stg_instagram__media",
        "stg_instagram__insights_daily",
        "stg_instagram__insights_totals",
    ),
    "tiktok_organic": (
        "stg_tiktok_organic__profile_stats",
        "stg_tiktok_organic__videos_organic",
    ),
    "youtube": (
        "stg_youtube__channel_stats",
        "stg_youtube__videos",
        "stg_youtube__video_daily_analytics",
    ),
    "pinterest": (
        "stg_pinterest__boards",
        "stg_pinterest__pins",
        "stg_pinterest__board_insights",
    ),
    "ga4": (
        "stg_ga4__daily_stats",
        "stg_ga4__page_analytics",
        "stg_ga4__event_analytics",
    ),
    "gtm": ("stg_gtm__containers", "stg_gtm__tags", "stg_gtm__triggers"),
}

# Conditional investment marts: appended by the caller only when the client has
# every connector in INVESTMENT_NEEDS enabled (spec A1 whitelist).
INVESTMENT_NEEDS: frozenset[str] = frozenset({"meta", "tiktok", "google"})
INVESTMENT_MARTS: tuple[str, ...] = (
    "int_unified_spend",
    "ad_spend_summary",
    "campaign_performance",
)


@dataclass(frozen=True)
class Plan:
    """Deterministic dbt selection for one client run."""

    models: tuple[str, ...]
    investment: bool


def build_plan(enabled: Iterable[str]) -> Plan:
    """Build the dbt select for a client given its enabled connectors.

    Returns the staging models of every known enabled connector (in ``enabled``
    order, duplicates collapsed) followed by the always-present
    MONITORING_CHAIN. Connectors without a mapping are ignored — nothing
    outside the staging whitelist is ever dragged in.
    """
    seen: set[str] = set()
    models: list[str] = []
    for connector in enabled:
        if connector in seen or connector not in CONNECTOR_MODELS:
            continue
        seen.add(connector)
        models.extend(CONNECTOR_MODELS[connector])
    models.extend(MONITORING_CHAIN)
    return Plan(models=tuple(models), investment=INVESTMENT_NEEDS <= seen)


def run_status(connectors_ok: int, connectors_failed: int, dbt_status: str) -> str:
    """Decide the run verdict from extraction AND transformation outcomes.

    The run is ``failed`` when any connector failed or dbt failed; ``success``
    only when both sides are clean (spec A3). dbt always runs, so a
    zero-connector client is still ``success`` when the chain-only dbt run
    succeeds.
    """
    if connectors_failed > 0 or dbt_status == "failed":
        return "failed"
    return "success"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m agency_analytics.pipeline_plan",
        description="Pure dbt select and run-status decisions for the pipeline.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Print the dbt select as JSON")
    plan_parser.add_argument(
        "--connectors",
        default="",
        help="Comma-separated list of enabled connector names",
    )

    status_parser = subparsers.add_parser("status", help="Print the run verdict as JSON")
    status_parser.add_argument("--ok", type=int, required=True)
    status_parser.add_argument("--failed", type=int, required=True)
    status_parser.add_argument("--dbt-status", required=True)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point: print one JSON line for the requested subcommand."""
    args = _parse_args(argv)
    if args.command == "plan":
        enabled = [connector for connector in args.connectors.split(",") if connector]
        plan = build_plan(enabled)
        print(json.dumps({"models": list(plan.models), "investment": plan.investment}))
    else:
        verdict = run_status(args.ok, args.failed, args.dbt_status)
        print(json.dumps({"status": verdict}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
