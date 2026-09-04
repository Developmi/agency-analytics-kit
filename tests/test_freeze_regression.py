"""Tests for the anti-freeze regression detector (spec IG6, design D9).

The module under test (``agency_analytics.freeze_regression``) does not exist
yet — this file is written FIRST so the focused run fails (RED, collection
error) until it lands.

The detector is pure: every test feeds in-memory row dicts (or a tiny CSV) and
asserts the structured findings. No DB, no docker, no network is touched; the
only DB-facing entry point is asserted to SKIP when no DSN is provided.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Sequence

from agency_analytics.freeze_regression import (
    CONNECTOR_CHECKS,
    INSTAGRAM_TOTAL_VALUE_ONLY,
    FrozenMetric,
    Skipped,
    TableCheck,
    assert_no_total_value_cols,
    check_rows,
    check_table_via_db,
    frozen_columns,
    rows_from_csv,
)

# Wired Instagram daily check: report_date | reach | follower_count only (WU2,
# IG1-R1). The tv-only names are the account-insights metrics the API serves as
# window scalars (IG6-R2), never as per-day columns.
IG_CHECK = CONNECTOR_CHECKS["instagram"]


def _ig_rows(
    *,
    dates: Sequence[str],
    reach: Sequence[int],
    follower_count: int | Sequence[int] = 300,
) -> list[dict[str, Any]]:
    """IG-shaped daily rows: report_date | reach | follower_count."""
    counts = (
        [follower_count] * len(dates) if isinstance(follower_count, int) else list(follower_count)
    )
    return [
        {"report_date": day, "reach": r, "follower_count": c}
        for day, r, c in zip(dates, reach, counts)
    ]


def test_healthy_multi_date_daily_table_is_clean() -> None:
    """A daily table whose must-vary columns actually vary is NOT flagged."""
    rows = _ig_rows(
        dates=["2026-08-01", "2026-08-02", "2026-08-03"],
        reach=[1200, 531, 789],
        follower_count=[300, 301, 302],
    )
    report = check_rows(rows, IG_CHECK)
    assert report.ok is True
    assert report.frozen == ()
    assert report.guard_offenders == ()
    assert "report_date" in report.columns and "reach" in report.columns


def test_frozen_must_vary_column_reports_column_value_and_key_count() -> None:
    """A must-vary metric constant over >=2 dates is caught with its value."""
    rows = _ig_rows(
        dates=["2026-08-01", "2026-08-02", "2026-08-03"],
        reach=[512, 512, 512],
        follower_count=[300, 301, 302],
    )
    report = check_rows(rows, IG_CHECK)
    assert report.ok is False
    assert report.frozen == (FrozenMetric(column="reach", value=512, key_count=3),)
    assert report.as_dict()["frozen"] == [{"column": "reach", "value": 512, "key_count": 3}]


def test_single_date_is_not_frozen() -> None:
    """One distinct date/window is never enough to declare a freeze."""
    rows = _ig_rows(dates=["2026-08-01"], reach=[512], follower_count=300)
    assert frozen_columns(rows, IG_CHECK.key_cols, IG_CHECK.vary_cols) == []
    report = check_rows(rows, IG_CHECK)
    assert report.ok is True


def test_min_distinct_keys_threshold_is_parametrizable() -> None:
    """The N of spec IG6-R1 is a parameter, not a hardcoded 2."""
    rows = _ig_rows(
        dates=["2026-08-01", "2026-08-02"],
        reach=[512, 512],
        follower_count=[300, 301],
    )
    assert frozen_columns(rows, IG_CHECK.key_cols, IG_CHECK.vary_cols, min_distinct_keys=3) == []
    assert frozen_columns(rows, IG_CHECK.key_cols, IG_CHECK.vary_cols) == ["reach"]
    strict_check = replace(IG_CHECK, min_distinct_keys=3)
    assert check_rows(rows, strict_check).ok is True


def test_all_null_metric_is_absence_not_freeze() -> None:
    """NULLs mirror SQL count(DISTINCT)=0: absence, never fan-out corruption."""
    rows = [
        {"report_date": "2026-08-01", "reach": None, "follower_count": 300},
        {"report_date": "2026-08-02", "reach": None, "follower_count": 301},
        {"report_date": "2026-08-03", "reach": None, "follower_count": 302},
    ]
    report = check_rows(rows, IG_CHECK)
    assert report.ok is True
    assert report.frozen == ()


def test_metric_present_on_one_date_is_not_frozen() -> None:
    """A metric recorded once (rest NULL) is not a constant fan-out."""
    rows = [
        {"report_date": "2026-08-01", "reach": 512, "follower_count": 300},
        {"report_date": "2026-08-02", "reach": None, "follower_count": 301},
        {"report_date": "2026-08-03", "reach": None, "follower_count": 302},
    ]
    report = check_rows(rows, IG_CHECK)
    assert report.ok is True
    assert report.frozen == ()


def test_constant_across_two_present_dates_still_frozen_with_null_row() -> None:
    """Non-NULL occurrences decide: 512 on 2 dates = frozen, NULL row ignored."""
    rows = [
        {"report_date": "2026-08-01", "reach": 512, "follower_count": 300},
        {"report_date": "2026-08-02", "reach": 512, "follower_count": 301},
        {"report_date": "2026-08-03", "reach": None, "follower_count": 302},
    ]
    report = check_rows(rows, IG_CHECK)
    assert report.ok is False
    assert report.frozen == (FrozenMetric(column="reach", value=512, key_count=2),)


def test_separation_guard_returns_tv_offenders_in_daily_columns() -> None:
    """total_value-only names present in a daily schema are the offenders."""
    daily_cols = ["report_date", "reach", "views", "likes", "follower_count"]
    offenders = assert_no_total_value_cols(daily_cols, INSTAGRAM_TOTAL_VALUE_ONLY)
    assert offenders == ["views", "likes"]
    clean = assert_no_total_value_cols(
        ["report_date", "reach", "follower_count"], INSTAGRAM_TOTAL_VALUE_ONLY
    )
    assert clean == []


def test_guard_flags_legacy_frozen_ig_table_shape() -> None:
    """The real bug shape (obs #530) fails through the full table check."""
    rows = [
        {
            "report_date": "2026-08-01",
            "reach": 1200,
            "follower_count": 300,
            "views": 7690,
            "likes": 133,
            "comments": 3,
            "shares": 5,
            "saves": 5,
        },
        {
            "report_date": "2026-08-02",
            "reach": 531,
            "follower_count": 301,
            "views": 7690,
            "likes": 133,
            "comments": 3,
            "shares": 5,
            "saves": 5,
        },
        {
            "report_date": "2026-08-03",
            "reach": 789,
            "follower_count": 302,
            "views": 7690,
            "likes": 133,
            "comments": 3,
            "shares": 5,
            "saves": 5,
        },
    ]
    report = check_rows(rows, IG_CHECK)
    assert report.guard_offenders == ("views", "likes", "comments", "shares", "saves")
    assert report.ok is False


def test_empty_dataset_is_vacuously_clean() -> None:
    """No rows -> no date to judge: the gate does not fail on absence of data."""
    report = check_rows([], IG_CHECK)
    assert report.ok is True
    assert report.frozen == ()
    assert report.guard_offenders == ()
    assert report.as_dict()["ok"] is True


def test_only_declared_vary_columns_are_checked() -> None:
    """A constant non-metric column is not in vary_cols and is never reported."""
    rows = [
        {"report_date": "2026-08-01", "reach": 1, "follower_count": 300, "note": "same"},
        {"report_date": "2026-08-02", "reach": 2, "follower_count": 301, "note": "same"},
        {"report_date": "2026-08-03", "reach": 3, "follower_count": 302, "note": "same"},
    ]
    report = check_rows(rows, IG_CHECK)
    assert report.ok is True
    assert report.frozen == ()


def test_multiple_frozen_columns_reported_in_declared_order() -> None:
    """Findings follow vary_cols order, so the report is deterministic."""
    rows = _ig_rows(
        dates=["2026-08-01", "2026-08-02", "2026-08-03"],
        reach=[512, 512, 512],
        follower_count=[300, 300, 300],
    )
    report = check_rows(rows, IG_CHECK)
    assert report.frozen == (
        FrozenMetric(column="reach", value=512, key_count=3),
        FrozenMetric(column="follower_count", value=300, key_count=3),
    )


def test_detector_is_parametrizable_for_other_connectors() -> None:
    """Same primitives, foreign connector shape: no Instagram name is baked in.

    Hypothetical facebook daily shape used only to prove the parametrization
    mechanism (IG6-R1). Real FB/YT wiring happens when those daily tables come
    into scope; inventing their constants here would be drift.
    """
    facebook_like = TableCheck(
        connector="facebook",
        schema="raw_facebook",
        table="page_insights_daily",
        key_cols=("date",),
        vary_cols=("organic_reach", "total_reach"),
        tv_only_cols=("page_lifetime_likes", "page_views_total"),
    )
    rows = [
        {"date": "2026-08-01", "organic_reach": 100, "total_reach": 55},
        {"date": "2026-08-02", "organic_reach": 200, "total_reach": 55},
        {"date": "2026-08-03", "organic_reach": 150, "total_reach": 55},
    ]
    report = check_rows(rows, facebook_like)
    assert report.connector == "facebook"
    assert report.schema == "raw_facebook"
    assert report.table == "page_insights_daily"
    assert report.as_dict()["table"] == "raw_facebook.page_insights_daily"
    assert report.frozen == (FrozenMetric(column="total_reach", value=55, key_count=3),)
    assert report.guard_offenders == ()
    assert report.ok is False


def test_composite_window_key_identity_is_supported() -> None:
    """Multi-column keys (video_id + date) work: distinct key tuples are counted."""
    window_like = TableCheck(
        connector="youtube",
        schema="raw_youtube",
        table="video_daily_analytics",
        key_cols=("video_id", "date"),
        vary_cols=("views",),
        tv_only_cols=(),
    )
    rows = [
        {"video_id": "v1", "date": "2026-08-01", "views": 10},
        {"video_id": "v1", "date": "2026-08-02", "views": 10},
        {"video_id": "v2", "date": "2026-08-01", "views": 10},
    ]
    report = check_rows(rows, window_like)
    assert report.frozen == (FrozenMetric(column="views", value=10, key_count=3),)
    assert report.ok is False


def test_csv_rows_feed_the_same_detector(tmp_path) -> None:
    """CSV entry point: string values, same freeze semantics."""
    csv_file = tmp_path / "insights_daily.csv"
    csv_file.write_text(
        "report_date,reach,follower_count\n"
        "2026-08-01,500,300\n"
        "2026-08-02,500,301\n"
        "2026-08-03,500,302\n",
        encoding="utf-8",
    )
    rows = list(rows_from_csv(csv_file))
    assert len(rows) == 3
    report = check_rows(rows, IG_CHECK)
    assert report.ok is False
    assert report.frozen == (FrozenMetric(column="reach", value="500", key_count=3),)
    assert report.frozen[0].value != 500  # CSV has no types: str is not int


def test_db_backed_check_skips_without_dsn() -> None:
    """No DSN -> Skipped verdict, never an import/connection attempt (D9)."""
    result = check_table_via_db(None, IG_CHECK)
    assert isinstance(result, Skipped)
    assert "DSN" in result.reason
    assert "docker" in result.reason
