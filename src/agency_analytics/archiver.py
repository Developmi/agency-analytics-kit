"""Append-only archive builders for raw-tenant ``_history`` tables (design D1-D7).

Pure module — no ``dlt`` import, no DB, no I/O: every function derives
in-memory SQL and flat-row structures that connector mains execute through
``pipeline.sql_client()`` right after ``pipeline.run`` (D3 seam; real
execution validated in WU4). Archive tables are named ``<source>_history``
(NOM-R1), live in the tenant raw schema (TEN-R1), carry a per-run
``captured_at`` TIMESTAMPTZ that is never part of the idempotency key
(ARC-R3), and dedupe by natural key with ``ON CONFLICT DO NOTHING`` (ARC-R4).

Zero-row contract: ``insert_on_conflict`` returns ``(None, ())`` when there
are no rows so callers skip the statement (ARC-S4/CATCH-R3) instead of issuing
an empty query, which Postgres would reject.

Determinism (NFR-3): flatteners preserve payload order and project a stable
column order, so identical captured payloads produce identical row lists and a
re-run archives the same natural-key set (ARC-S2/S2b).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

# ─── Archive table names (NOM-R1; child keeps the dlt ``__`` separator) ─────
INSIGHTS_TOTALS_HISTORY = "insights_totals_history"
INSIGHTS_TOTALS_BREAKDOWNS_HISTORY = "insights_totals__breakdowns_history"
FOLLOWER_COUNT_HISTORY = "follower_count_history"
PROFILE_STATS_HISTORY = "profile_stats_history"

# ─── Natural keys per archive table (ARC-R4) ────────────────────────────────
TOTALS_NATURAL_KEYS: tuple[str, ...] = ("date_start", "date_end")
BREAKDOWNS_NATURAL_KEYS: tuple[str, ...] = (
    "date_start",
    "date_end",
    "metric",
    "breakdown",
    "dimension_value",
)
FOLLOWER_NATURAL_KEYS: tuple[str, ...] = ("report_date",)
PROFILE_NATURAL_KEYS: tuple[str, ...] = ("report_date",)


def quote_ident(identifier: str) -> str:
    """Quote a Postgres identifier, doubling embedded double quotes (D3).

    Dataset/table names may come from client config; quoting keeps a hostile
    value inside one token so the statement never gains a second command.
    """
    return '"' + identifier.replace('"', '""') + '"'


def schema_table(dataset: str, table: str) -> str:
    """Quoted ``"dataset"."table"`` pair for the tenant raw schema (TEN-R1)."""
    return f"{quote_ident(dataset)}.{quote_ident(table)}"


# ─── Per-table column specs: ordered (name, SQL declaration) pairs ──────────
# DDL column order and flattener row-key order must agree so the INSERT column
# list lines up with the flattened rows (captured_at always last, ARC-R3).


def insights_totals_columns(metric_columns: Sequence[str]) -> list[tuple[str, str]]:
    """Specs for ``insights_totals_history``: window key + metric columns.

    ``metric_columns`` is injected by the connector main (single source of
    truth: run_instagram.py TOTAL_VALUE_COMMON_METRICS + GATED_METRICS) so the
    archive never duplicates the metric list (design open question Q1).
    """
    columns: list[tuple[str, str]] = [
        ("date_start", "date NOT NULL"),
        ("date_end", "date NOT NULL"),
    ]
    columns.extend((name, "bigint") for name in metric_columns)
    columns.append(("captured_at", "timestamptz NOT NULL"))
    return columns


def insights_totals_breakdowns_columns() -> list[tuple[str, str]]:
    """Specs for ``insights_totals__breakdowns_history`` (child, G2)."""
    return [
        ("date_start", "date NOT NULL"),
        ("date_end", "date NOT NULL"),
        ("metric", "text NOT NULL"),
        ("breakdown", "text NOT NULL"),
        ("dimension_value", "text NOT NULL"),
        ("value", "bigint"),
        ("captured_at", "timestamptz NOT NULL"),
    ]


def follower_count_columns() -> list[tuple[str, str]]:
    """Specs for ``follower_count_history`` (net-new series table, D5)."""
    return [
        ("report_date", "date NOT NULL"),
        ("follower_count", "bigint"),
        ("captured_at", "timestamptz NOT NULL"),
    ]


def profile_stats_columns() -> list[tuple[str, str]]:
    """Specs for ``profile_stats_history`` (TikTok organic)."""
    return [
        ("report_date", "date NOT NULL"),
        ("follower_count", "bigint"),
        ("following_count", "bigint"),
        ("likes_count", "bigint"),
        ("video_count", "bigint"),
        ("captured_at", "timestamptz NOT NULL"),
    ]


def create_table_ddl(
    dataset: str,
    table: str,
    columns: Sequence[tuple[str, str]],
    natural_keys: Sequence[str],
) -> str:
    """``CREATE TABLE IF NOT EXISTS`` with the natural-key PRIMARY KEY (D4).

    Idempotent DDL executed by the archiver on its first run; ``captured_at``
    is a plain NOT NULL column and never appears in ``natural_keys`` (ARC-R3).
    """
    column_lines = ",\n    ".join(f"{quote_ident(name)} {decl}" for name, decl in columns)
    primary_key = ", ".join(quote_ident(key) for key in natural_keys)
    return (
        f"CREATE TABLE IF NOT EXISTS {schema_table(dataset, table)} (\n"
        f"    {column_lines},\n"
        f"    PRIMARY KEY ({primary_key})\n"
        ");"
    )


def insert_on_conflict(
    dataset: str,
    table: str,
    columns: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    conflict_cols: Sequence[str] | None = None,
) -> tuple[str | None, tuple[Any, ...]]:
    """One multi-row ``INSERT ... ON CONFLICT DO NOTHING`` with ``%s`` params.

    - ``columns``: ordered INSERT column list; every row must provide each.
    - ``rows``: empty -> returns ``(None, ())`` so the caller skips the
      statement (ARC-S4/CATCH-R3); zero invented rows ever reach the DB.
    - ``conflict_cols``: the arbiter; defaults to ``columns``. Callers pass the
      natural keys (ARC-R4) — never ``captured_at`` (ARC-R3).
    - Values are bound (params tuple), never interpolated (D3 threat row);
      dataset/table identifiers are quoted via :func:`quote_ident`.

    Returns ``(sql, params)`` ready for ``sql_client.execute_sql(sql, *params)``.
    """
    if not rows:
        return None, ()
    arbiter = conflict_cols if conflict_cols is not None else columns
    column_list = ", ".join(quote_ident(name) for name in columns)
    conflict_target = ", ".join(quote_ident(name) for name in arbiter)
    placeholders = ", ".join(["%s"] * len(columns))
    values = ", ".join(f"({placeholders})" for _ in rows)
    params = tuple(value for row in rows for value in (row[name] for name in columns))
    sql = (
        f"INSERT INTO {schema_table(dataset, table)} ({column_list}) "
        f"VALUES {values} ON CONFLICT ({conflict_target}) DO NOTHING;"
    )
    return sql, params


# ─── Flatteners: captured payloads -> flat INSERT rows (per-run timestamp) ──


def flatten_totals(
    captured_totals: Iterable[Mapping[str, Any]],
    metric_columns: Sequence[str],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    """Project captured totals rows into ``insights_totals_history`` rows.

    Each captured row keeps its window identity (``date_start``/``date_end``),
    the metric values in ``metric_columns`` order — a metric absent from the
    API payload flattens to ``None`` (never invented) — and the run's
    ``captured_at``. Nested ``breakdowns`` are ignored here (child rows come
    from :func:`flatten_breakdowns`).
    """
    rows: list[dict[str, Any]] = []
    for row in captured_totals:
        flat: dict[str, Any] = {
            "date_start": row["date_start"],
            "date_end": row["date_end"],
        }
        for metric in metric_columns:
            flat[metric] = row.get(metric)
        flat["captured_at"] = captured_at
        rows.append(flat)
    return rows


def flatten_breakdowns(
    captured_totals: Iterable[Mapping[str, Any]],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    """Flatten child breakdown rows; the parent window travels into each (G2).

    The child natural key is ``(date_start, date_end, metric, breakdown,
    dimension_value)`` (ARC-R4); child payloads alone lack the window, so it is
    re-read from the captured parent row. Parents with no ``breakdowns`` list
    contribute nothing. Order follows the payload, keeping the result
    deterministic (ARC-S2b/NFR-3).
    """
    rows: list[dict[str, Any]] = []
    for parent in captured_totals:
        window = {"date_start": parent["date_start"], "date_end": parent["date_end"]}
        for child in parent.get("breakdowns") or []:
            rows.append(
                {
                    **window,
                    "metric": child["metric"],
                    "breakdown": child["breakdown"],
                    "dimension_value": child["dimension_value"],
                    "value": child.get("value"),
                    "captured_at": captured_at,
                }
            )
    return rows


def flatten_follower(
    captured_follower: Iterable[Mapping[str, Any]],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    """Project the IG follower series into ``follower_count_history`` rows.

    Captured entries are ``get_insights`` rows (``report_date`` + reach +
    follower_count); only the follower series is archived (D5), so ``reach``
    never travels. Rows whose ``follower_count`` is None are dropped — an
    all-NULL series archives nothing (CATCH-R3).
    """
    rows: list[dict[str, Any]] = []
    for entry in captured_follower:
        if entry.get("follower_count") is None:
            continue
        rows.append(
            {
                "report_date": entry["report_date"],
                "follower_count": entry["follower_count"],
                "captured_at": captured_at,
            }
        )
    return rows


def flatten_profile_stats(
    captured_profile: Iterable[Mapping[str, Any]],
    captured_at: datetime,
) -> list[dict[str, Any]]:
    """Map TikTok organic ``profile_stats`` rows keyed by ``report_date``.

    The captured row is already flat (``report_date`` + counts); the flattener
    adds the run ``captured_at`` and fixes the canonical column order.
    """
    rows: list[dict[str, Any]] = []
    for row in captured_profile:
        rows.append(
            {
                "report_date": row["report_date"],
                "follower_count": row["follower_count"],
                "following_count": row["following_count"],
                "likes_count": row["likes_count"],
                "video_count": row["video_count"],
                "captured_at": captured_at,
            }
        )
    return rows
