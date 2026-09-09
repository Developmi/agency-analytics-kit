"""Synthetic SDD-D archive glue replica for the wipe-sim (no real APIs).

Executes the EXACT post-``pipeline.run`` statements the connector mains run
(design D6; run_instagram.py:714-742 / run_tiktok_organic.py:273-294) against
the real sim postgres through a real dlt ``pipeline.sql_client()``:

* per table one idempotent ``CREATE TABLE IF NOT EXISTS`` (design D4), then
* one guarded multi-row ``INSERT ... ON CONFLICT DO NOTHING`` with bound
  ``%s`` params (archiver.insert_on_conflict, D3 — the single multi-row
  statement per table whose shape is asserted offline in WU1 and whose real
  Postgres execution is validated HERE: a re-run over the same natural keys
  must exit 0 and leave counts/``captured_at`` untouched).

The captured payloads are NOT re-fetched (no API): they come from
``seed_synthetic``'s corrida getters — the same single source that loads the
live tables — so the archive always consumes exactly what the simulated run
served. Two corridas model the horizon drift of spec ARC-S3:

* corrida K    — horizon still serves window W + CUR: everything is archived.
* corrida K+1  — live replace already evicted W: only CUR keys are re-sent and
  skipped by DO NOTHING; W survives ONLY in the ``_history`` tables.

``--skip-follower`` simulates the CATCH-S1 partial-run state: the archive
crashes (as if) right after the totals + child inserts and before the follower
insert, leaving a SUBSET of the run's natural keys archived; the retry without
the flag completes the missing follower keys without duplicating.

Usage (inside the isolated pipeline container, env already wired):

    python /app/wipesim/archive_synthetic.py --client acme --connector instagram --corrida K
    python /app/wipesim/archive_synthetic.py --client acme --connector tiktok_organic --corrida K+1
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/app/src")
sys.path.insert(0, "/app/src/connectors")

import dlt  # noqa: E402
import run_instagram as ig_connector  # noqa: E402 — metric columns source of truth
import seed_synthetic as seed  # noqa: E402 — corrida fixtures (single source)

from agency_analytics import archiver, client_config  # noqa: E402


def _archive_table(client, dataset: str, table: str, columns, natural_keys, rows) -> None:
    """Byte-for-byte mirror of run_instagram._archive_table (WU2): idempotent
    DDL then a guarded multi-row insert; ``insert_on_conflict`` returns
    ``(None, ())`` for zero rows so no empty statement reaches Postgres."""
    client.execute_sql(archiver.create_table_ddl(dataset, table, columns, natural_keys))
    column_names = [name for name, _ in columns]
    sql, params = archiver.insert_on_conflict(dataset, table, column_names, rows, natural_keys)
    if sql is not None:
        client.execute_sql(sql, *params)


def _archive_instagram(
    client, client_id: str, corrida: str, captured_at, skip_follower: bool
) -> None:
    """Replica of the run_instagram.py:714-742 glue for the synthetic capture."""
    dataset = client_config.raw_dataset(client_id, "instagram")
    common = ig_connector.TOTAL_VALUE_COMMON_METRICS
    gated = ig_connector.TOTAL_VALUE_GATED_METRICS
    metric_columns = (*common, *gated)
    totals = seed.ig_totals_corrida(client_id, corrida)
    _archive_table(
        client,
        dataset,
        archiver.INSIGHTS_TOTALS_HISTORY,
        archiver.insights_totals_columns(metric_columns),
        archiver.TOTALS_NATURAL_KEYS,
        archiver.flatten_totals(totals, metric_columns, captured_at),
    )
    _archive_table(
        client,
        dataset,
        archiver.INSIGHTS_TOTALS_BREAKDOWNS_HISTORY,
        archiver.insights_totals_breakdowns_columns(),
        archiver.BREAKDOWNS_NATURAL_KEYS,
        archiver.flatten_breakdowns(totals, captured_at),
    )
    if skip_follower:
        # CATCH-S1: simulate the archive failing right after the totals + child
        # inserts — the follower keys of this run are left unarchived.
        print("[archive] simulated mid-archive crash before the follower insert")
        return
    _archive_table(
        client,
        dataset,
        archiver.FOLLOWER_COUNT_HISTORY,
        archiver.follower_count_columns(),
        archiver.FOLLOWER_NATURAL_KEYS,
        archiver.flatten_follower(seed.ig_follower_corrida(client_id, corrida), captured_at),
    )


def _archive_tiktok_organic(client, client_id: str, corrida: str, captured_at) -> None:
    """Replica of the run_tiktok_organic.py:273-294 glue (profile_stats)."""
    dataset = client_config.raw_dataset(client_id, "tiktok_organic")
    profile_rows = archiver.flatten_profile_stats(
        seed.profile_stats_corrida(client_id, corrida), captured_at
    )
    client.execute_sql(
        archiver.create_table_ddl(
            dataset,
            archiver.PROFILE_STATS_HISTORY,
            archiver.profile_stats_columns(),
            archiver.PROFILE_NATURAL_KEYS,
        )
    )
    sql, params = archiver.insert_on_conflict(
        dataset,
        archiver.PROFILE_STATS_HISTORY,
        [name for name, _ in archiver.profile_stats_columns()],
        profile_rows,
        archiver.PROFILE_NATURAL_KEYS,
    )
    if sql is not None:
        client.execute_sql(sql, *params)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Synthetic SDD-D archive glue replica (wipe-sim WU4)"
    )
    parser.add_argument("--client", required=True, help="client_id from /app/clients")
    parser.add_argument("--connector", required=True, choices=("instagram", "tiktok_organic"))
    parser.add_argument("--corrida", required=True, choices=seed.CORRIDAS)
    parser.add_argument(
        "--skip-follower",
        action="store_true",
        help="CATCH-S1: simulate a mid-archive crash before the IG follower insert",
    )
    args = parser.parse_args(argv)

    # One captured_at per run, identical for every row of every table (ARC-R3).
    captured_at = datetime.now(timezone.utc)
    pipeline = dlt.pipeline(
        pipeline_name=f"{args.connector}_{args.client}",
        destination="postgres",
        dataset_name=client_config.raw_dataset(args.client, args.connector),
    )
    with pipeline.sql_client() as client:
        if args.connector == "instagram":
            _archive_instagram(client, args.client, args.corrida, captured_at, args.skip_follower)
        else:
            _archive_tiktok_organic(client, args.client, args.corrida, captured_at)
    print(
        f"[archive] {args.client}/{args.connector} corrida {args.corrida} OK"
        f" (captured_at={captured_at.isoformat()})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
