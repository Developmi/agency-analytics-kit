"""Synthetic dlt seed for the WU10 wipe-sim (no real APIs).

Replicates the exact runtime contract of the connector mains (design D6/D10,
spec A-R1/A-R2/A-R3) but with local fixture rows instead of live API payloads:

* ``pipeline_name = f"{connector}_{client_id}"``  (A-R2 — dlt state key)
* ``dataset_name  = client_config.raw_dataset(client_id, connector)``
  (A-R1 — ``raw_<connector>_<client_id>`` via the shared naming helper)
* resources ``ads`` / ``campaigns`` with ``write_disposition="replace"``
  (A-R3 — per-tenant full replace is the isolation boundary)

Fixtures mirror the FULL column surface each connector's dbt staging models
reference (``stg_<conn>__ads`` / ``stg_<conn>__campaigns``), so per-client
``dbt run`` builds are real against the seeded raw tables. Row values are
client-prefixed (``ad_id`` etc.) so physical isolation is assertable in SQL
(a row that belongs to acme can never appear in nike's namespace).

Usage (inside the isolated pipeline container, env already wired):

    python /app/wipesim/seed_synthetic.py --client acme
    python /app/wipesim/seed_synthetic.py --client acme --connector meta   # rerun

The optional ``--connector`` override exists for fixture tables that are NOT
enabled in the client YAML (e.g. ``raw_instagram_acme.insights_daily`` for
the freeze-regression DB gate, design D8); callers must pass it explicitly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from typing import Any, Iterator

sys.path.insert(0, "/app/src")

import dlt  # noqa: E402

from agency_analytics import client_config  # noqa: E402

# Row counts per client so A-S1/A-S2/A-S3 have distinct, assertable states.
# acme: 2 rows per table; nike: 3 rows per table.
COUNTS: dict[str, int] = {"acme": 2, "nike": 3}


def _prefix(client_id: str, kind: str, i: int) -> str:
    return f"{client_id}-{kind}-{i:03d}"


def _row_ad(client_id: str, connector: str, i: int) -> dict[str, Any]:
    """One synthetic ads row covering every column stg_<conn>__ads selects."""
    return {
        "ad_id": _prefix(client_id, f"{connector}-ad", i),
        "ad_group_id": _prefix(client_id, f"{connector}-ag", i),
        "campaign_id": _prefix(client_id, f"{connector}-cmp", i),
        "ad_name": f"{client_id} {connector} ad {i}",
        "status": "ACTIVE" if i % 2 == 0 else "PAUSED",
        "spend": float(10 * (i + 1) + (1 if client_id == "acme" else 2)),
        "spend_usd": float(10 * (i + 1) + (1 if client_id == "acme" else 2)),
        "impressions": 1000 * (i + 1),
        "clicks": 100 * (i + 1),
        "reach": 900 * (i + 1),
        "frequency": 1.1 + i,
        "cpm": 10.0 + i,
        "cpc": 0.9 + i,
        "ctr": 0.01 + i / 1000,
        "average_cpc": 0.85 + i,
        "conversions": float(i + 1),
        "cost_per_conversion": 12.0 + i,
        "date": dt.date(2026, 9, 1),
    }


def _row_campaign(client_id: str, connector: str, i: int) -> dict[str, Any]:
    """One synthetic campaign row covering stg_<conn>__campaigns columns."""
    spend = float(100 * (i + 1) + (3 if client_id == "acme" else 4))
    return {
        "campaign_id": _prefix(client_id, f"{connector}-cmp", i),
        "campaign_name": f"{client_id} {connector} campaign {i}",
        "status": "ACTIVE" if i % 2 == 0 else "PAUSED",
        "objective": "CONVERSIONS",
        "advertising_channel_type": "SEARCH",
        "budget": 500.0,
        "budget_type": "DAILY",
        "spend": spend,
        "spend_usd": spend,
        "impressions": 10000 * (i + 1),
        "clicks": 1000 * (i + 1),
        "reach": 9000 * (i + 1),
        "frequency": 1.05 + i,
        "cpm": 9.0 + i,
        "cpc": 0.8 + i,
        "ctr": 0.02 + i / 1000,
        "average_cpc": 0.85 + i,
        "conversions": float(i + 1),
        "cost_per_conversion": 12.0 + i,
        "start_date": dt.date(2026, 8, 1),
        "end_date": dt.date(2026, 12, 31),
        "date": dt.date(2026, 9, 1),
    }


def _rows_instagram_daily(client_id: str, n: int) -> Iterator[dict[str, Any]]:
    """Narrow IG daily shape (freeze-regression fixture, spec E-R3 / D8).

    ``reach`` and ``follower_count`` vary per day so the anti-freeze gate
    reports ``ok``; the caller can UPDATE reach to a constant to prove the
    frozen branch (report.ok False) against a real tenant schema.
    """
    for i in range(n):
        yield {
            "report_date": dt.date(2026, 9, 1) + dt.timedelta(days=i),
            "reach": 500 + i * 37,
            "follower_count": 1000 + i * 11,
        }


def _resource(name: str, rows: list[dict[str, Any]]):
    hints = {
        col: {"data_type": "date"}
        for col in ("date", "start_date", "end_date", "report_date")
        if col in rows[0]
    }

    @dlt.resource(name=name, write_disposition="replace", columns=hints)
    def resource() -> Iterator[dict[str, Any]]:
        yield from rows

    return resource


def build_source(client_id: str, connector: str) -> list[Any]:
    """One dlt source per (client, connector): ads + campaigns, replace."""
    n = COUNTS.get(client_id, 2)
    if connector == "instagram":
        # Freeze fixture: only the narrow daily table is needed.
        return [_resource("insights_daily", list(_rows_instagram_daily(client_id, n)))]
    ads = [_row_ad(client_id, connector, i) for i in range(n)]
    campaigns = [_row_campaign(client_id, connector, i) for i in range(n)]
    return [_resource("ads", ads), _resource("campaigns", campaigns)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Synthetic dlt seed (wipe-sim WU10)")
    parser.add_argument("--client", required=True, help="client_id from /app/clients")
    parser.add_argument(
        "--connector",
        default=None,
        help="override: seed a single connector even if disabled in the YAML "
        "(freeze fixture); default = YAML enabled connectors",
    )
    args = parser.parse_args(argv)

    client = client_config.load_client(args.client)
    connectors = [args.connector] if args.connector else client_config.enabled_connectors(client)
    if not connectors:
        print(f"[seed] client {args.client}: no connectors — nothing to load")
        return 0

    for connector in connectors:
        pipeline = dlt.pipeline(
            pipeline_name=f"{connector}_{args.client}",
            destination="postgres",
            dataset_name=client_config.raw_dataset(args.client, connector),
        )
        info = pipeline.run(build_source(args.client, connector))
        print(f"[seed] {args.client}/{connector}: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
