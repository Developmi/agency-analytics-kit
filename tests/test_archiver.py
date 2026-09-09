"""Unit tests for the pure archive builders (design D1-D7, WU1 of SDD-D).

RED-first work unit: this file references ``agency_analytics.archiver`` which
does not exist yet at write time (collection error); the module is created in
the GREEN step.

Covers WU1 acceptance from tasks 1.1-1.3:
- flatteners for the four archive payloads with a per-run ``captured_at``
  (ARC-S1 full horizon; ARC-S2b + NFR-3 child-key determinism; G2 parent
  window travels into child rows; CATCH-R3/CATCH-S2 empty and all-NULL);
- ``_history`` naming (NOM-R1), natural-key DDL per table with ``captured_at``
  outside the PK (ARC-R4/ARC-R3, D4/D5);
- multi-row ``INSERT ... ON CONFLICT DO NOTHING`` with ``%s`` bound params and
  quoted dataset/table identifiers (D3; SQL/DML threat row);
- zero rows => DDL only, no INSERT statement (ARC-S4/CATCH-R3);
- offline E-era registration guard (NOM-E1 lift over D NOM-S1): the four
  ``_history`` tables are registered as dbt sources (sources.yml inside the
  existing per-connector blocks) and read by the three organic marts;
  pipeline_plan and freeze_regression stay ``_history``-free (retained NOM-R2)
  and no model may select the registered-but-unused breakdowns child source
  (NOM-E-S2).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

from agency_analytics import archiver

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DBT_MODELS_DIR = os.path.join(REPO_ROOT, "src", "dbt_project", "models")

# Metric column list mirrors the connector single source of truth
# (run_instagram.py TOTAL_VALUE_COMMON_METRICS + TOTAL_VALUE_GATED_METRICS).
# Tests keep a local copy so the pure module stays decoupled from connectors.
COMMON_METRICS = (
    "views",
    "likes",
    "comments",
    "shares",
    "saves",
    "total_interactions",
    "accounts_engaged",
    "replies",
    "reposts",
)
GATED_METRICS = ("follows_and_unfollows", "profile_links_taps")
METRIC_COLUMNS = COMMON_METRICS + GATED_METRICS

RUN_TIME = datetime(2026, 9, 7, 12, 0, 0, tzinfo=timezone.utc)


def _totals_row(date_start, date_end, values, breakdowns=()):
    """One captured parent totals row (get_insights_totals yield shape)."""
    row = {"date_start": date_start, "date_end": date_end, "breakdowns": list(breakdowns)}
    for metric in METRIC_COLUMNS:
        row[metric] = values.get(metric)
    return row


def _child(metric, dimension_value, value=None, breakdown="media_product_type"):
    """One captured breakdown child entry (run_instagram._fetch_breakdown shape)."""
    return {
        "metric": metric,
        "breakdown": breakdown,
        "dimension_value": dimension_value,
        "value": value,
    }


# ─── flatten_totals (ARC-S1 full horizon; absent metric -> NULL) ───────────


class TestFlattenTotals:
    def test_full_horizon_flattens_each_window_with_single_captured_at(self):
        captured_totals = [
            _totals_row("2026-06-09", "2026-07-08", {"views": 11, "likes": 22}),
            _totals_row("2026-07-09", "2026-08-07", {"views": 33, "likes": 44}),
            _totals_row("2026-08-08", "2026-09-06", {"views": 55, "likes": 66}),
        ]
        rows = archiver.flatten_totals(captured_totals, METRIC_COLUMNS, RUN_TIME)
        assert len(rows) == 3
        assert [r["date_start"] for r in rows] == [
            "2026-06-09",
            "2026-07-09",
            "2026-08-08",
        ]
        assert [r["date_end"] for r in rows] == ["2026-07-08", "2026-08-07", "2026-09-06"]
        assert rows[0]["views"] == 11
        assert rows[2]["views"] == 55
        assert rows[0]["likes"] == 22
        # ARC-R3: one identical captured_at timestamp for every row of the run.
        assert all(r["captured_at"] is RUN_TIME for r in rows)
        # Canonical column order: window keys, metric columns, then captured_at.
        assert list(rows[0]) == ["date_start", "date_end", *METRIC_COLUMNS, "captured_at"]

    def test_metric_absent_in_payload_is_null_never_invented(self):
        captured = [_totals_row("2026-08-08", "2026-09-06", {"views": 5})]
        rows = archiver.flatten_totals(captured, ("views", "likes"), RUN_TIME)
        assert rows[0]["views"] == 5
        assert rows[0]["likes"] is None

    def test_double_flatten_is_deterministic(self):
        # NFR-3: identical captured payload -> identical row list/order, the
        # property that makes ON CONFLICT DO NOTHING idempotent on re-run.
        captured = [
            _totals_row("2026-08-08", "2026-09-06", {"views": 55, "likes": 66}),
            _totals_row("2026-07-09", "2026-08-07", {"views": 33}),
        ]
        assert archiver.flatten_totals(captured, METRIC_COLUMNS, RUN_TIME) == (
            archiver.flatten_totals(captured, METRIC_COLUMNS, RUN_TIME)
        )


# ─── flatten_breakdowns (G2 parent window; ARC-S2b determinism) ─────────────


class TestFlattenBreakdowns:
    def test_parent_window_travels_into_each_child_row(self):
        captured_totals = [
            _totals_row(
                "2026-06-09",
                "2026-07-08",
                {"views": 1},
                breakdowns=[
                    _child("views", "REEL", 40),
                    _child("views", "POST", 12),
                ],
            ),
            _totals_row(
                "2026-07-09",
                "2026-08-07",
                {"likes": 1},
                breakdowns=[
                    _child("follows_and_unfollows", "FOLLOWER", 3, breakdown="follow_type")
                ],
            ),
        ]
        rows = archiver.flatten_breakdowns(captured_totals, RUN_TIME)
        assert len(rows) == 3
        assert rows[0] == {
            "date_start": "2026-06-09",
            "date_end": "2026-07-08",
            "metric": "views",
            "breakdown": "media_product_type",
            "dimension_value": "REEL",
            "value": 40,
            "captured_at": RUN_TIME,
        }
        # Second parent window reaches its own child rows (G2 key rebuild).
        assert rows[2]["date_start"] == "2026-07-09"
        assert rows[2]["date_end"] == "2026-08-07"
        assert rows[2]["metric"] == "follows_and_unfollows"
        assert rows[2]["breakdown"] == "follow_type"
        assert rows[2]["dimension_value"] == "FOLLOWER"
        assert all(r["captured_at"] is RUN_TIME for r in rows)

    def test_identical_payloads_produce_identical_child_key_sets(self):
        # ARC-S2b: two identical payloads (a re-run) produce the same natural
        # keys -> DO NOTHING leaves the archive duplicate-free.
        payload = [
            _totals_row(
                "2026-08-08",
                "2026-09-06",
                {"views": 1},
                breakdowns=[_child("views", "REEL", 5), _child("views", "STORY", 3)],
            )
        ]
        first = archiver.flatten_breakdowns(payload, RUN_TIME)
        second = archiver.flatten_breakdowns(payload, RUN_TIME)
        assert first == second
        keys = {
            (r["date_start"], r["date_end"], r["metric"], r["breakdown"], r["dimension_value"])
            for r in first
        }
        # No duplicate natural key within or across the flatten passes.
        assert len(keys) == len(first) == 2

    def test_row_without_breakdowns_contributes_no_child_rows(self):
        captured = [
            _totals_row("2026-08-08", "2026-09-06", {"views": 1}, breakdowns=[]),
            {"date_start": "2026-07-09", "date_end": "2026-08-07", "views": 2},
        ]
        assert archiver.flatten_breakdowns(captured, RUN_TIME) == []


# ─── flatten_follower (G4 series; CATCH-R3 all-NULL) ───────────────────────


class TestFlattenFollower:
    def test_projects_follower_series_and_drops_reach(self):
        captured = [
            {"report_date": "2026-08-08", "reach": 100, "follower_count": 2500},
            {"report_date": "2026-08-09", "reach": 105, "follower_count": 2501},
        ]
        rows = archiver.flatten_follower(captured, RUN_TIME)
        assert len(rows) == 2
        assert rows[0] == {
            "report_date": "2026-08-08",
            "follower_count": 2500,
            "captured_at": RUN_TIME,
        }
        # reach is never archived (D5): only report_date + follower_count travel.
        assert "reach" not in rows[0]
        assert rows[1]["report_date"] == "2026-08-09"

    def test_none_follower_rows_are_dropped_not_invented(self):
        captured = [
            {"report_date": "2026-08-08", "reach": None, "follower_count": None},
            {"report_date": "2026-08-09", "reach": 1, "follower_count": 500},
        ]
        rows = archiver.flatten_follower(captured, RUN_TIME)
        assert len(rows) == 1
        assert rows[0]["report_date"] == "2026-08-09"
        assert rows[0]["follower_count"] == 500

    def test_empty_follower_capture_archives_nothing(self):
        # CATCH-S2/CATCH-R3: no servable series -> zero rows, nothing invented.
        assert archiver.flatten_follower([], RUN_TIME) == []


# ─── flatten_profile_stats (TikTok organic, report_date key) ───────────────


class TestFlattenProfileStats:
    def test_maps_profile_stats_row_with_report_date_key(self):
        captured = [
            {
                "report_date": "2026-09-07",
                "follower_count": 1200,
                "following_count": 300,
                "likes_count": 9000,
                "video_count": 14,
            }
        ]
        rows = archiver.flatten_profile_stats(captured, RUN_TIME)
        assert rows == [
            {
                "report_date": "2026-09-07",
                "follower_count": 1200,
                "following_count": 300,
                "likes_count": 9000,
                "video_count": 14,
                "captured_at": RUN_TIME,
            }
        ]

    def test_empty_profile_capture_archives_nothing(self):
        assert archiver.flatten_profile_stats([], RUN_TIME) == []


# ─── quote_ident (SQL/DML threat row: hostile identifiers stay one token) ───


class TestQuoteIdent:
    def test_quotes_plain_identifier(self):
        assert archiver.quote_ident("insights_totals_history") == '"insights_totals_history"'

    def test_escapes_embedded_quotes_in_hostile_identifier(self):
        hostile = 'raw_instagram_x"; DROP TABLE insights_totals; --'
        expected = '"raw_instagram_x""; DROP TABLE insights_totals; --"'
        assert archiver.quote_ident(hostile) == expected

    def test_hostile_dataset_stays_single_ddl_statement(self):
        ddl = archiver.create_table_ddl(
            'raw_instagram_x"; DROP TABLE',
            archiver.FOLLOWER_COUNT_HISTORY,
            archiver.follower_count_columns(),
            archiver.FOLLOWER_NATURAL_KEYS,
        )
        assert ddl.startswith(
            'CREATE TABLE IF NOT EXISTS "raw_instagram_x""; DROP TABLE"."follower_count_history" ('
        )
        assert ddl.endswith('PRIMARY KEY ("report_date")\n);')
        # Balanced double quotes prove the hostile text never broke out.
        assert ddl.count('"') % 2 == 0


# ─── create_table_ddl (ARC-R4 natural PK; ARC-R3 captured_at outside) ──────


class TestCreateTableDdl:
    def test_follower_count_history_ddl_exact(self):
        ddl = archiver.create_table_ddl(
            "raw_instagram_acme",
            archiver.FOLLOWER_COUNT_HISTORY,
            archiver.follower_count_columns(),
            archiver.FOLLOWER_NATURAL_KEYS,
        )
        assert ddl == (
            'CREATE TABLE IF NOT EXISTS "raw_instagram_acme"."follower_count_history" (\n'
            '    "report_date" date NOT NULL,\n'
            '    "follower_count" bigint,\n'
            '    "captured_at" timestamptz NOT NULL,\n'
            '    PRIMARY KEY ("report_date")\n'
            ");"
        )

    def test_insights_totals_history_ddl_exact_with_metric_columns(self):
        ddl = archiver.create_table_ddl(
            "raw_instagram_acme",
            archiver.INSIGHTS_TOTALS_HISTORY,
            archiver.insights_totals_columns(("views", "likes")),
            archiver.TOTALS_NATURAL_KEYS,
        )
        assert ddl == (
            'CREATE TABLE IF NOT EXISTS "raw_instagram_acme"."insights_totals_history" (\n'
            '    "date_start" date NOT NULL,\n'
            '    "date_end" date NOT NULL,\n'
            '    "views" bigint,\n'
            '    "likes" bigint,\n'
            '    "captured_at" timestamptz NOT NULL,\n'
            '    PRIMARY KEY ("date_start", "date_end")\n'
            ");"
        )

    def test_breakdowns_history_ddl_exact_composite_key(self):
        ddl = archiver.create_table_ddl(
            "raw_instagram_acme",
            archiver.INSIGHTS_TOTALS_BREAKDOWNS_HISTORY,
            archiver.insights_totals_breakdowns_columns(),
            archiver.BREAKDOWNS_NATURAL_KEYS,
        )
        assert ddl == (
            'CREATE TABLE IF NOT EXISTS "raw_instagram_acme"'
            '."insights_totals__breakdowns_history" (\n'
            '    "date_start" date NOT NULL,\n'
            '    "date_end" date NOT NULL,\n'
            '    "metric" text NOT NULL,\n'
            '    "breakdown" text NOT NULL,\n'
            '    "dimension_value" text NOT NULL,\n'
            '    "value" bigint,\n'
            '    "captured_at" timestamptz NOT NULL,\n'
            '    PRIMARY KEY ("date_start", "date_end", "metric", "breakdown", '
            '"dimension_value")\n'
            ");"
        )

    def test_profile_stats_history_ddl_exact(self):
        ddl = archiver.create_table_ddl(
            "raw_tiktok_organic_nike",
            archiver.PROFILE_STATS_HISTORY,
            archiver.profile_stats_columns(),
            archiver.PROFILE_NATURAL_KEYS,
        )
        assert ddl == (
            'CREATE TABLE IF NOT EXISTS "raw_tiktok_organic_nike"."profile_stats_history" (\n'
            '    "report_date" date NOT NULL,\n'
            '    "follower_count" bigint,\n'
            '    "following_count" bigint,\n'
            '    "likes_count" bigint,\n'
            '    "video_count" bigint,\n'
            '    "captured_at" timestamptz NOT NULL,\n'
            '    PRIMARY KEY ("report_date")\n'
            ");"
        )

    def test_captured_at_never_part_of_natural_key_any_table(self):
        # ARC-R3 across the four archive tables: captured_at is per-run and
        # must never appear in the idempotency arbiter.
        specs = [
            (
                archiver.INSIGHTS_TOTALS_HISTORY,
                archiver.insights_totals_columns(METRIC_COLUMNS),
                archiver.TOTALS_NATURAL_KEYS,
            ),
            (
                archiver.INSIGHTS_TOTALS_BREAKDOWNS_HISTORY,
                archiver.insights_totals_breakdowns_columns(),
                archiver.BREAKDOWNS_NATURAL_KEYS,
            ),
            (
                archiver.FOLLOWER_COUNT_HISTORY,
                archiver.follower_count_columns(),
                archiver.FOLLOWER_NATURAL_KEYS,
            ),
            (
                archiver.PROFILE_STATS_HISTORY,
                archiver.profile_stats_columns(),
                archiver.PROFILE_NATURAL_KEYS,
            ),
        ]
        for _table, columns, natural_keys in specs:
            assert "captured_at" not in natural_keys
            ddl = archiver.create_table_ddl("raw_instagram_acme", _table, columns, natural_keys)
            pk_section = ddl.split("PRIMARY KEY (", 1)[1].split(")", 1)[0]
            assert "captured_at" not in pk_section


# ─── insert_on_conflict (D3: single multi-row, %s params, DO NOTHING) ───────


class TestInsertOnConflict:
    def test_multi_row_insert_with_bound_params_and_do_nothing(self):
        columns = ["report_date", "follower_count", "captured_at"]
        rows = [
            {"report_date": "2026-08-08", "follower_count": 2500, "captured_at": RUN_TIME},
            {"report_date": "2026-08-09", "follower_count": 2501, "captured_at": RUN_TIME},
        ]
        sql, params = archiver.insert_on_conflict(
            "raw_instagram_acme",
            archiver.FOLLOWER_COUNT_HISTORY,
            columns,
            rows,
            conflict_cols=archiver.FOLLOWER_NATURAL_KEYS,
        )
        assert sql == (
            'INSERT INTO "raw_instagram_acme"."follower_count_history" '
            '("report_date", "follower_count", "captured_at") '
            "VALUES (%s, %s, %s), (%s, %s, %s) "
            'ON CONFLICT ("report_date") DO NOTHING;'
        )
        assert params == ("2026-08-08", 2500, RUN_TIME, "2026-08-09", 2501, RUN_TIME)

    def test_values_are_bound_never_interpolated(self):
        columns = ["dimension_value", "value"]
        hostile = 'REEL"); DROP TABLE insights_totals_history; --'
        sql, params = archiver.insert_on_conflict(
            "raw_instagram_acme",
            archiver.INSIGHTS_TOTALS_BREAKDOWNS_HISTORY,
            columns,
            [{"dimension_value": hostile, "value": 5}],
            conflict_cols=("dimension_value",),
        )
        assert hostile not in sql
        assert "DROP TABLE" not in sql
        assert sql.count("%s") == 2
        assert params == (hostile, 5)

    def test_conflict_cols_default_to_all_inserted_columns(self):
        sql, _params = archiver.insert_on_conflict(
            "raw_instagram_acme", "x_history", ["a", "b"], [{"a": 1, "b": 2}]
        )
        assert sql.endswith('ON CONFLICT ("a", "b") DO NOTHING;')

    def test_dataset_and_table_identifiers_quoted(self):
        sql, _params = archiver.insert_on_conflict(
            'raw_x"; DROP SCHEMA public; --',
            't"; DROP TABLE x; --',
            ["a"],
            [{"a": 1}],
        )
        assert sql.startswith(
            'INSERT INTO "raw_x""; DROP SCHEMA public; --"."t""; DROP TABLE x; --" ("a") '
            'VALUES (%s) ON CONFLICT ("a") DO NOTHING;'
        )
        assert sql.count('"') % 2 == 0

    def test_empty_rows_produce_no_insert_statement(self):
        # ARC-S4/CATCH-R3: zero rows -> (None, ()) so the caller skips the
        # statement (an empty query would be an error, not a clean no-op).
        sql, params = archiver.insert_on_conflict(
            "raw_instagram_acme",
            archiver.FOLLOWER_COUNT_HISTORY,
            ["report_date", "follower_count", "captured_at"],
            [],
            conflict_cols=archiver.FOLLOWER_NATURAL_KEYS,
        )
        assert sql is None
        assert params == ()

    def test_zero_rows_from_empty_payload_means_ddl_only(self):
        rows = archiver.flatten_follower([], RUN_TIME)
        assert rows == []
        sql, _params = archiver.insert_on_conflict(
            "raw_instagram_acme",
            archiver.FOLLOWER_COUNT_HISTORY,
            ["report_date", "follower_count", "captured_at"],
            rows,
            conflict_cols=archiver.FOLLOWER_NATURAL_KEYS,
        )
        assert sql is None


# ─── NOM-R1: _history table names (child __ separator preserved) ────────────


class TestArchiveTableNames:
    @pytest.mark.parametrize(
        ("constant", "expected"),
        [
            ("INSIGHTS_TOTALS_HISTORY", "insights_totals_history"),
            (
                "INSIGHTS_TOTALS_BREAKDOWNS_HISTORY",
                "insights_totals__breakdowns_history",
            ),
            ("FOLLOWER_COUNT_HISTORY", "follower_count_history"),
            ("PROFILE_STATS_HISTORY", "profile_stats_history"),
        ],
    )
    def test_history_suffix_names(self, constant, expected):
        assert getattr(archiver, constant) == expected


# ─── E registration guard: NOM-E1 lift over D NOM-S1 (dbt sources only) ──────


class TestNoDbtRegistration:
    """Offline repo guard for archive-table registration (E-era, NOM-E1).

    E deliberately registers the four D ``_history`` tables as dbt sources
    (spec #620 NOM-E1): sources.yml lists all four inside the existing
    per-connector blocks, schema.yml describes/guards the three organic marts,
    and each mart reads exactly its consumed archive source. The retained
    NOM-R2 clauses still hold: pipeline_plan.py and freeze_regression.py stay
    ``_history``-free, and NO model may select the registered-but-unused
    breakdowns child source (NOM-E-S2).
    """

    # The only dbt files allowed to reference the E `_history` tables:
    # sources.yml registers all four; schema.yml guards the three marts; each
    # of the three marts reads exactly its consumed archive source.
    _ALLOWED_HISTORY_FILES = frozenset(
        {
            "sources.yml",
            "schema.yml",
            os.path.join("marts", "organic_instagram_totals.sql"),
            os.path.join("marts", "organic_instagram_daily.sql"),
            os.path.join("marts", "organic_tiktok_profile_daily.sql"),
        }
    )

    # NOM-E-S2: the dlt child `__` table is registered-but-unused in v1 — a
    # legal dbt source that no model may select yet.
    _BREAKDOWNS_SELECT = "source('raw_instagram', 'insights_totals__breakdowns_history')"

    def _repo_texts(self):
        texts = []
        for root, _dirs, files in os.walk(DBT_MODELS_DIR):
            for name in files:
                path = os.path.join(root, name)
                with open(path, encoding="utf-8") as f:
                    texts.append((path, f.read()))
        for relative in (
            "src/agency_analytics/pipeline_plan.py",
            "src/agency_analytics/freeze_regression.py",
        ):
            path = os.path.join(REPO_ROOT, relative)
            with open(path, encoding="utf-8") as f:
                texts.append((path, f.read()))
        return texts

    def test_dbt_history_references_stay_within_the_e_lift(self):
        offenders = []
        breakdowns_selectors = []
        models_dir = str(DBT_MODELS_DIR)
        for path, content in self._repo_texts():
            if path.startswith(models_dir + os.sep):
                relative = os.path.relpath(path, DBT_MODELS_DIR)
                if "_history" in content and relative not in self._ALLOWED_HISTORY_FILES:
                    offenders.append(path)
                if self._BREAKDOWNS_SELECT in content:
                    breakdowns_selectors.append(path)
            elif "_history" in content:
                # Retained NOM-R2: no archive entries in the plan resolver or
                # the freeze-regression connector checks.
                offenders.append(path)
        assert offenders == [], offenders
        assert breakdowns_selectors == [], breakdowns_selectors
