"""Anti-freeze regression for connector daily tables (spec IG6, design D9).

Pure, dependency-free detection that a metric column which MUST vary with its
date/window key is not frozen to a single value, plus the structural guard
that window-scoped ``total_value`` columns never appear in a daily stream.

Background (diagnosis obs #530): ``raw_instagram.insights_daily`` accumulated
729 rows where ``reach`` had 535 distinct values but every window-scoped
metric column (``views``, ``likes``, ...) carried exactly 1 distinct value:
the connector read the API's ``metric_type=total_value`` scalar once per
window and fanned it out over every daily row. Meta only serves ``reach`` as a
per-day ``time_series``; the other metrics are window scalars.

This module is the shared, additive artifact (IG8): pure functions over
in-memory rows (or CSV), parametrized per connector through
:class:`TableCheck`. Instagram is wired now (IG6 enforced); Facebook/YouTube
reuse the same primitives when their daily tables come into scope — nothing
here hardcodes an Instagram column name in the logic.

Freeze semantics mirror SQL ``count(DISTINCT)`` over non-NULL values:

* a must-vary column is FROZEN when exactly one distinct non-NULL value exists
  and that value is present on ``>= min_distinct_keys`` distinct keys
  (dates/windows). NULL-only columns are NOT frozen: absence of a metric is
  recorded as NULL, never as a fabricated value (IG3-R2), so all-NULL is
  absence, not fan-out corruption.
* the separation guard flags every ``total_value``-only metric name found
  among the table's columns (IG6-R2, IG1-R3).

DB-backed invocation is a thin psycopg2 wrapper that runs ONLY when a DSN is
provided (the isolated docker gate, apply WU5); without a DSN it returns
:class:`Skipped` and never imports a driver, so the module stays importable
and pure in offline contexts (unit runs, verify phase).

Example (pure, in memory)::

    report = check_rows(rows, CONNECTOR_CHECKS["instagram"])
    if not report.ok:
        print(report.as_dict())   # structured findings for the verify phase

``report.ok`` is the boolean surface: False iff at least one frozen metric or
one guard offender was found.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

# total_value-only metric names per connector: never valid as a daily column.
# The Instagram list is transcribed from the official Meta metric table (docs
# updated 2026-06-16, obs #530): these are exactly the account-insights
# metrics the API serves as window scalars. Keep in sync with the
# TOTAL_VALUE_*_METRICS constants in agency_analytics/ig_probe.py.
INSTAGRAM_TOTAL_VALUE_ONLY: tuple[str, ...] = (
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "total_interactions",
    "accounts_engaged",
    "replies",
    "reposts",
    "follows_and_unfollows",
    "profile_links_taps",
)

# Declared per-day metric columns after the daily/totals split (WU2, IG1-R1).
# follower_count stays in daily only while the probe confirms a time_series
# shape; the regression simply checks whatever the wiring declares.
INSTAGRAM_DAILY_VARY_COLS: tuple[str, ...] = ("reach", "follower_count")


@dataclass(frozen=True)
class TableCheck:
    """Declarative regression target for one connector's daily table.

    ``key_cols`` are the date/window identity columns (``report_date``, or a
    composite window identity); ``vary_cols`` are the metric columns that MUST
    vary with the key; ``tv_only_cols`` are the window-scoped total_value
    metric names that may never appear as columns of this table.
    ``min_distinct_keys`` is the N of spec IG6-R1 (default 2).
    """

    connector: str
    schema: str
    table: str
    key_cols: tuple[str, ...]
    vary_cols: tuple[str, ...]
    tv_only_cols: tuple[str, ...]
    min_distinct_keys: int = 2


# Per-connector wiring. ``instagram`` is enforced now; a future FB/YT entry is
# just another TableCheck over the foreign daily table (parametrization proven
# in tests/test_freeze_regression.py with foreign shapes).
CONNECTOR_CHECKS: dict[str, TableCheck] = {
    "instagram": TableCheck(
        connector="instagram",
        schema="raw_instagram",
        table="insights_daily",
        key_cols=("report_date",),
        vary_cols=INSTAGRAM_DAILY_VARY_COLS,
        tv_only_cols=INSTAGRAM_TOTAL_VALUE_ONLY,
    ),
}


@dataclass(frozen=True)
class FrozenMetric:
    """One frozen must-vary column and the evidence against it."""

    column: str
    value: Any
    key_count: int


@dataclass(frozen=True)
class TableReport:
    """Structured outcome of a full table regression (freeze + guard)."""

    connector: str
    schema: str
    table: str
    columns: tuple[str, ...]
    frozen: tuple[FrozenMetric, ...]
    guard_offenders: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """False iff a frozen metric or a separation-guard offender was found."""
        return not self.frozen and not self.guard_offenders

    def as_dict(self) -> dict[str, Any]:
        """JSON-serializable view for the pipeline/verify helper."""
        return {
            "connector": self.connector,
            "table": f"{self.schema}.{self.table}",
            "ok": self.ok,
            "columns": list(self.columns),
            "frozen": [
                {"column": finding.column, "value": finding.value, "key_count": finding.key_count}
                for finding in self.frozen
            ],
            "guard_offenders": list(self.guard_offenders),
        }


@dataclass(frozen=True)
class Skipped:
    """Verdict when the DB-backed gate is not run (offline: no DSN)."""

    reason: str


def _row_key(row: Mapping[str, Any], key_cols: Sequence[str]) -> tuple[Any, ...] | None:
    """Key tuple for a row, or None when any key column is missing/NULL."""
    key = tuple(row.get(column) for column in key_cols)
    if any(part is None for part in key):
        return None
    return key


def _freeze_findings(
    rows: Iterable[Mapping[str, Any]],
    key_cols: Sequence[str],
    vary_cols: Sequence[str],
    min_distinct_keys: int,
) -> tuple[FrozenMetric, ...]:
    """Frozen must-vary columns among ``rows`` (non-NULL semantics, see docstring).

    Findings follow ``vary_cols`` declaration order so the report is
    deterministic regardless of input row order.
    """
    material = list(rows)
    findings: list[FrozenMetric] = []
    for column in vary_cols:
        distinct_keys: set[tuple[Any, ...]] = set()
        distinct_values: set[Any] = set()
        for row in material:
            value = row.get(column)
            if value is None:
                continue
            key = _row_key(row, key_cols)
            if key is None:
                continue
            distinct_keys.add(key)
            distinct_values.add(value)
        if len(distinct_values) == 1 and len(distinct_keys) >= min_distinct_keys:
            findings.append(
                FrozenMetric(
                    column=column,
                    value=next(iter(distinct_values)),
                    key_count=len(distinct_keys),
                )
            )
    return tuple(findings)


def frozen_columns(
    rows: Iterable[Mapping[str, Any]],
    key_cols: Sequence[str],
    vary_cols: Sequence[str],
    min_distinct_keys: int = 2,
) -> list[str]:
    """Names of must-vary columns frozen to one value over >= N distinct keys.

    ``rows`` are in-memory row mappings (column name -> scalar value),
    ``key_cols`` the date/window identity, ``vary_cols`` the metric columns
    that must vary. ``min_distinct_keys`` is the spec N (default 2 dates).
    """
    return [
        finding.column for finding in _freeze_findings(rows, key_cols, vary_cols, min_distinct_keys)
    ]


def assert_no_total_value_cols(
    cols: Sequence[str],
    tv_only: Sequence[str],
) -> list[str]:
    """Separation guard (IG6-R2): tv-only names present among ``cols``.

    Name kept from design D9's contract. It does NOT raise: it returns the
    offender list so the runner aggregates it into the report — a non-empty
    result IS the failure, and callers express it via ``report.ok``.
    """
    present = set(cols)
    return [name for name in tv_only if name in present]


def check_rows(
    rows: Iterable[Mapping[str, Any]],
    check: TableCheck,
    *,
    columns: Sequence[str] | None = None,
) -> TableReport:
    """Run the full regression for one table over in-memory rows.

    The separation guard inspects the table's columns: the union of the row
    keys when ``columns`` is omitted (row data available), or the explicit
    schema column list (the DB-backed wrapper passes information_schema).
    """
    material = list(rows)
    if columns is None:
        observed: list[str] = []
        for row in material:
            for column in row:
                if column not in observed:
                    observed.append(column)
        inspected = observed
    else:
        inspected = list(columns)
    frozen = _freeze_findings(material, check.key_cols, check.vary_cols, check.min_distinct_keys)
    offenders = assert_no_total_value_cols(inspected, check.tv_only_cols)
    return TableReport(
        connector=check.connector,
        schema=check.schema,
        table=check.table,
        columns=tuple(inspected),
        frozen=frozen,
        guard_offenders=tuple(offenders),
    )


def rows_from_csv(path: str | Path) -> Iterator[dict[str, str]]:
    """Load a CSV file into row dicts (string values) for the pure checks.

    CSV has no types: values arrive as strings and are reported as such.
    Distinctness and the reported frozen value use the CSV's own
    representation.
    """
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    return iter(rows)


def check_table_via_db(
    dsn: str | None,
    check: TableCheck,
) -> TableReport | Skipped:
    """DB-backed thin wrapper: schema columns + key/vary rows -> pure checks.

    Executed only when ``dsn`` is provided (the isolated docker gate, apply
    WU5). With ``dsn=None`` it returns :class:`Skipped` without importing the
    driver, keeping offline/unit imports pure (design D9: no DSN => skip).
    """
    if dsn is None:
        return Skipped(
            reason=(
                "no DSN: the DB-backed anti-freeze gate only runs in the "
                "isolated docker environment (apply WU5); offline it is skipped."
            )
        )
    import psycopg2  # deferred: only reachable with an explicit DSN
    from psycopg2 import sql

    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (check.schema, check.table),
            )
            schema_columns = [row[0] for row in cursor.fetchall()]
            selected = [*check.key_cols, *check.vary_cols]
            query = sql.SQL("SELECT {} FROM {}.{}").format(
                sql.SQL(", ").join(map(sql.Identifier, selected)),
                sql.Identifier(check.schema),
                sql.Identifier(check.table),
            )
            cursor.execute(query)
            rows = [dict(zip(selected, row_tuple)) for row_tuple in cursor.fetchall()]
    finally:
        conn.close()
    return check_rows(rows, check, columns=schema_columns)
