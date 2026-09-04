"""Instagram connector tests — daily/totals split (spec IG1-IG3, design D1-D7).

RED-first rewrite (apply WU2, tasks 2.1-2.7). The pre-fix tests asserted the
frozen fan-out behavior (``test_insights_success``, dual-format merge/empty):
window-scoped ``total_value`` scalars repeated on every daily row. Spec
IG6-R3/Scenario 6.3 orders those tests rewritten into the NEW expectations so
they FAIL against the pre-fix connector and turn green only against the
corrected resources:

* ``insights_daily`` carries ONLY ``{report_date, reach, follower_count}``
  (IG1-R1/R3): no total_value-derived column, no fan-out (IG1-R2).
* ``insights_totals`` yields one deterministic row per trailing <=30d window
  (``date_start``/``date_end`` anchored to a fixed ``run_ts``, NFR-2) with the
  probe-confirmed total_value metrics; breakdowns nest per axis into the dlt
  child table ``insights_totals__breakdowns`` (D3); empty API datasets are
  absence, never 0 (IG3-R2, Scenario 2.4); gated metrics only >= 100 followers
  (IG3-R1, Scenario 3.2).

Every API interaction is mocked with a scripted fake ``requests.get`` — no
live call, no DB write. ``now`` is frozen (a datetime subclass) so window
boundaries are deterministic (frozen-now pattern, design NFR-2).
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
import run_instagram as ig
import yaml

MODULE = "run_instagram"

NOW = datetime(2026, 9, 4, 0, 0, 0, tzinfo=timezone.utc)
NOW_TS = int(NOW.timestamp())
DAY = 86400

# Probe-transcribed total_value set (obs #537, ig_probe totals) and the legacy
# columns that must never reappear in a daily row (IG1-R3, IG3-R3).
TV_AND_LEGACY_COLS = {
    "views",
    "profile_views",
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
    "website_clicks",
    "email_contacts",
    "get_directions_clicks",
    "phone_call_clicks",
}

COMMON_BASE = {
    "views": 1000,
    "likes": 200,
    "comments": 50,
    "shares": 30,
    "saves": 25,
    "total_interactions": 500,
    "accounts_engaged": 300,
    "replies": 5,
    "reposts": 20,
}
# Media product type dimensions transcribed from the live probe (obs #537 #6).
MEDIA_DIMS = {
    "views": ["AD", "CAROUSEL_CONTAINER", "POST", "REEL", "STORY"],
    "likes": ["AD", "POST", "REEL", "STORY"],
    "comments": ["AD", "POST", "REEL"],
    "shares": ["AD", "POST", "REEL", "STORY"],
    "saves": ["AD", "POST", "REEL"],
    "total_interactions": ["AD", "POST", "REEL", "STORY"],
}
FOLLOW_DIMS = ["FOLLOWER", "NON_FOLLOWER"]


class _FrozenDatetime(datetime):
    """datetime subclass whose now() returns the fixed NOW (window determinism)."""

    @classmethod
    def now(cls, tz=None):  # noqa: N805 - classmethod mirror of datetime.now
        return NOW


def _freeze_now(monkeypatch) -> None:
    monkeypatch.setattr(f"{MODULE}.datetime", _FrozenDatetime)


def _mock_json(data):
    from unittest.mock import MagicMock

    from dlt.sources.helpers import requests

    m = MagicMock(spec=requests.Response)
    m.json.return_value = data
    return m


def _offset_days(params: dict[str, Any]) -> int:
    """Trailing offset of a window whose request ``until`` is given."""
    until = int(params["until"])
    return max(0, round((NOW_TS - until) / DAY))


def _date_range_series(since_ts: int, until_ts: int, value_fn: Callable[[datetime], int]):
    """Reach-style series: one values entry per calendar day in [since, until)."""
    since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
    until_dt = datetime.fromtimestamp(until_ts, tz=timezone.utc)
    values = []
    cursor = since_dt
    while cursor < until_dt:
        values.append(
            {"value": value_fn(cursor), "end_time": cursor.isoformat().replace("+00:00", "+0000")}
        )
        cursor += timedelta(days=1)
    return values


def _total_payload(name: str, value: int | None, *, breakdowns: list | None = None) -> dict:
    tv: dict[str, Any] = {"value": value}
    if breakdowns is not None:
        tv["breakdowns"] = breakdowns
    return {"name": name, "period": "day", "total_value": tv}


# ─── Daily resource (IG1): reach multi-window + follower_count recent ───────


def _daily_fake(*, record: list | None = None) -> Callable:
    """Scripted API fake for the daily resource.

    reach responds as a per-day series over every requested 30d window with a
    strictly monotonic value per calendar day; follower_count responds only in
    its 30-day request window (the docs/probe ceiling).
    """

    def mock_get(url, params=None):
        params = params or {}
        record.append(dict(params))
        metric = params["metric"]
        since_ts = int(params["since"])
        until_ts = int(params["until"])
        if metric == "reach":
            return _mock_json(
                {
                    "data": [
                        {
                            "name": "reach",
                            "period": "day",
                            "values": _date_range_series(
                                since_ts, until_ts, lambda d: d.toordinal() * 10 + 5
                            ),
                        }
                    ]
                }
            )
        if metric == "follower_count":
            return _mock_json(
                {
                    "data": [
                        {
                            "name": "follower_count",
                            "period": "day",
                            "values": _date_range_series(
                                since_ts, until_ts, lambda d: 700_000 + d.toordinal()
                            ),
                        }
                    ]
                }
            )
        return _mock_json({"data": []})

    return mock_get


def test_insights_daily_multi_window_multi_date_no_fanout(monkeypatch):
    """IG1 Scenario 1.1: rows equal the union of per-window reach dates, each
    with its own value — no scalar repeats across rows (no fan-out, IG1-R2)."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _daily_fake(record=record))

    source = ig.instagram_source("test_biz_456", "mock_token", insights_days_back=65)
    rows = list(source.resources["insights_daily"])

    # 65 calendar days in [NOW-65, NOW) across three 30d windows.
    assert len(rows) == 65
    reach_values = [row["reach"] for row in rows]
    assert len(set(reach_values)) == 65, "every date keeps its own reach value"
    assert [row["report_date"] for row in rows] == sorted(row["report_date"] for row in rows)

    # Every daily row exposes ONLY the narrow per-day keys (IG1-R1).
    for row in rows:
        assert set(row) <= {"report_date", "reach", "follower_count"}


def test_insights_daily_keeps_value_returned_for_each_date(monkeypatch):
    """IG1 Scenario 1.2: each date keeps the value its own window returned;
    the merge never lets a later window overwrite with a fabricated scalar."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _daily_fake(record=record))

    source = ig.instagram_source("test_biz_456", "mock_token", insights_days_back=65)
    rows = list(source.resources["insights_daily"])

    # Rebuild the per-date map exactly from the responses the fake served, then
    # prove the yielded rows carry precisely those per-date values.
    expected: dict[str, int] = {}
    for params in record:
        if params["metric"] != "reach":
            continue
        for value in _date_range_series(
            int(params["since"]), int(params["until"]), lambda d: d.toordinal() * 10 + 5
        ):
            expected[value["end_time"][:10]] = value["value"]
    assert len(expected) == 65
    for row in rows:
        assert row["reach"] == expected[row["report_date"]]


def test_insights_daily_never_calls_total_value(monkeypatch):
    """IG1-R3 + Scenario 1.3: the daily resource issues NO total_value call and
    yields NO total_value/legacy column (views/profile_views/legacy gone)."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _daily_fake(record=record))

    source = ig.instagram_source("test_biz_456", "mock_token", insights_days_back=65)
    rows = list(source.resources["insights_daily"])

    # Window calls are reach time_series only, plus one follower_count.
    assert [p["metric"] for p in record] == ["reach"] * 3 + ["follower_count"]
    assert all(p["metric_type"] == "time_series" for p in record)
    for row in rows:
        assert not (set(row) & TV_AND_LEGACY_COLS), f"tv/legacy column leaked: {row}"


def test_insights_daily_follower_count_only_recent_window(monkeypatch):
    """IG1-R1 (D6, probe): follower_count is populated only inside the recent
    30-day window where the API answers; older daily rows carry NULL."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _daily_fake(record=record))

    source = ig.instagram_source("test_biz_456", "mock_token", insights_days_back=65)
    rows = list(source.resources["insights_daily"])

    fc_window_start = (NOW - timedelta(days=30)).date().isoformat()
    older = [r for r in rows if r["report_date"] < fc_window_start]
    recent = [r for r in rows if r["report_date"] >= fc_window_start]
    assert len(older) == 35 and len(recent) == 30
    assert all(r["follower_count"] is None for r in older)
    assert all(r["follower_count"] is not None for r in recent)
    # The recent values match the API series exactly (per-date, real values).
    expected = {
        value["end_time"][:10]: value["value"]
        for value in _date_range_series(
            int((NOW - timedelta(days=30)).timestamp()),
            NOW_TS,
            lambda d: 700_000 + d.toordinal(),
        )
    }
    for row in recent:
        assert row["follower_count"] == expected[row["report_date"]]


# ─── Totals resource (IG2): windows, rows, breakdowns, gating, absence ──────


def test_totals_windows_are_deterministic_and_cover_horizon():
    """NFR-2/D2: windows split [now-H, now] into <=30d steps from the anchor;
    date_start/date_end are ISO boundaries with end = window_end - 1 day."""
    windows = ig._totals_windows(NOW, 90)
    assert len(windows) == 3
    assert [w["offset_days"] for w in windows] == [0, 30, 60]
    assert [w["date_start"] for w in windows] == [
        "2026-08-05",
        "2026-07-06",
        "2026-06-06",
    ]
    assert [w["date_end"] for w in windows] == [
        "2026-09-03",
        "2026-08-04",
        "2026-07-05",
    ]
    # Boundaries are contiguous and cover exactly [now-90, now-1].
    for w in windows:
        assert w["since_ts"] == int(
            (datetime.fromisoformat(w["date_start"]).replace(tzinfo=timezone.utc)).timestamp()
        )
        assert w["until_ts"] - w["since_ts"] <= 30 * DAY
    # Determinism: same anchor, same windows.
    assert ig._totals_windows(NOW, 90) == windows


def test_totals_windows_clamp_partial_window_at_horizon():
    """A horizon that is not a multiple of 30 clamps the oldest window to the
    horizon boundary instead of overshooting it."""
    windows = ig._totals_windows(NOW, 45)
    assert [w["offset_days"] for w in windows] == [0, 30]
    assert windows[-1]["date_start"] == (NOW - timedelta(days=45)).date().isoformat()
    # A 30-day horizon yields exactly one window.
    assert [w["offset_days"] for w in ig._totals_windows(NOW, 30)] == [0]


def _totals_fake(
    *,
    followers: int = 5000,
    record: list | None = None,
    common_empty_offsets: set[int] | None = None,
    common_error_offsets: set[int] | None = None,
    gated_empty: bool = False,
    breakdown_errors: set[tuple[int, str]] | None = None,
) -> Callable:
    """Scripted API fake for the totals resource.

    Common total_value metrics respond per window with window-distinct values
    (offset/30 * 10000 added); media_product_type breakdowns respond on every
    window; gated metrics and the follow_type breakdown only answer in the
    current (offset 0) window, mirroring the probe's max_history observations.
    """
    record = [] if record is None else record

    def _common_value(metric: str, offset: int) -> int:
        return COMMON_BASE[metric] + (offset // 30) * 10_000

    def mock_get(url, params=None):
        params = params or {}
        record.append({"url": url, **params})
        offset = _offset_days(params) if "until" in params else 0

        if "/insights" not in url:
            return _mock_json(
                {"id": "ig_biz_456", "username": "testbiz", "followers_count": followers}
            )

        metric = params["metric"]
        breakdown = params.get("breakdown")
        metric_type = params.get("metric_type")

        if metric_type == "total_value" and not breakdown:
            if "," in metric:
                if offset in (common_error_offsets or set()):
                    return _mock_json(
                        {"error": {"code": 100, "message": "invalid parameter batch"}}
                    )
                if offset in (common_empty_offsets or set()):
                    return _mock_json({"data": []})
                data = []
                for name in COMMON_BASE:
                    data.append(_total_payload(name, _common_value(name, offset)))
                return _mock_json({"data": data})
            # Single gated metric call (offset-0 window only in this fake).
            if gated_empty:
                return _mock_json({"data": []})
            return _mock_json(
                {"data": [_total_payload(metric, 7 if metric == "follows_and_unfollows" else 12)]}
            )

        if breakdown == "media_product_type":
            if (offset, "media_product_type") in (breakdown_errors or set()):
                return _mock_json({"error": {"code": 100, "message": "breakdown unsupported"}})
            data = []
            for name, dims in MEDIA_DIMS.items():
                base = _common_value(name, offset)
                breakdowns = [
                    {
                        "dimension_keys": ["media_product_type"],
                        "results": [
                            {"dimension_values": [dim], "value": base * 10 + i + 1}
                            for i, dim in enumerate(dims)
                        ],
                    }
                ]
                data.append(_total_payload(name, base, breakdowns=breakdowns))
            return _mock_json({"data": data})

        if breakdown == "follow_type":
            breakdowns = [
                {
                    "dimension_keys": ["follow_type"],
                    "results": [
                        {"dimension_values": [dim], "value": i + 1}
                        for i, dim in enumerate(FOLLOW_DIMS)
                    ],
                }
            ]
            return _mock_json(
                {"data": [_total_payload("follows_and_unfollows", 3, breakdowns=breakdowns)]}
            )

        return _mock_json({"data": []})

    return mock_get


def test_insights_totals_one_row_per_window(monkeypatch):
    """IG2 Scenario 2.1: one deterministic row per window (date_start/date_end)
    carrying that window's own total_value metrics — no daily expansion."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake(record=record))

    source = ig.instagram_source("test_biz_456", "mock_token", insights_days_back=10)
    rows = list(source.resources["insights_totals"])

    assert len(rows) == 3
    assert [r["date_start"] for r in rows] == ["2026-08-05", "2026-07-06", "2026-06-06"]
    assert [r["date_end"] for r in rows] == ["2026-09-03", "2026-08-04", "2026-07-05"]
    for row, offset in zip(rows, (0, 30, 60)):
        for metric, base in COMMON_BASE.items():
            assert row[metric] == base + (offset // 30) * 10_000, metric
        # Gated columns are present on every row; the eligible account answers
        # them only in the current window (probe max_history 0).
        if offset == 0:
            assert row["follows_and_unfollows"] == 7
            assert row["profile_links_taps"] == 12
        else:
            assert row["follows_and_unfollows"] is None
            assert row["profile_links_taps"] is None
        assert set(row) >= {
            "date_start",
            "date_end",
            *COMMON_BASE,
            "follows_and_unfollows",
            "profile_links_taps",
            "breakdowns",
        }


def test_insights_totals_rerun_is_identical(monkeypatch):
    """NFR-1: a re-run over the same anchored windows replaces with identical
    rows — replace whole-table semantics, never accumulating duplicates."""
    _freeze_now(monkeypatch)
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake())

    source = ig.instagram_source("test_biz_456", "mock_token")
    first = list(source.resources["insights_totals"])
    second = list(source.resources["insights_totals"])
    assert len(first) == 3
    assert first == second


def test_insights_totals_empty_window_is_absence_not_zero(monkeypatch):
    """IG2 Scenario 2.4 + IG3-R2: an empty data:[] window yields NO window row
    and no fabricated 0 anywhere; the other windows are unaffected."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(
        f"{MODULE}.requests.get", _totals_fake(record=record, common_empty_offsets={30})
    )

    source = ig.instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_totals"])

    assert [r["date_start"] for r in rows] == ["2026-08-05", "2026-06-06"]
    # No value in any row is an invented zero for an absent metric: every metric
    # value on surviving rows is either a real positive or None.
    for row in rows:
        for metric in COMMON_BASE:
            assert row[metric] is None or row[metric] > 0
    # The empty window was skipped before any breakdown call for it.
    mpt_offsets = {
        _offset_days(p)
        for p in record
        if p.get("metric_type") == "total_value" and p.get("breakdown") == "media_product_type"
    }
    assert 30 not in mpt_offsets


def test_insights_totals_below_follower_gate_skips_gated(monkeypatch):
    """IG3 Scenario 3.2: < 100 followers -> gated columns NULL, no gated metric
    call and no follow_type breakdown; media_product_type still fetched."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake(followers=50, record=record))

    source = ig.instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_totals"])

    assert len(rows) == 3
    for row in rows:
        assert row["follows_and_unfollows"] is None
        assert row["profile_links_taps"] is None

    gated_calls = [
        p
        for p in record
        if p.get("metric") in ("follows_and_unfollows", "profile_links_taps")
        and not p.get("breakdown")
    ]
    follow_type_calls = [p for p in record if p.get("breakdown") == "follow_type"]
    assert gated_calls == [] and follow_type_calls == []
    assert any(p.get("breakdown") == "media_product_type" for p in record)


def test_insights_totals_gated_empty_dataset_never_breaks(monkeypatch):
    """IG3-R2 + live obs #537: gated endpoints answering data:[] store NULL and
    the run completes; the follow_type breakdown is still fetched (live shape:
    empty follows_and_unfollows metric, populated follow_type dimensions)."""
    _freeze_now(monkeypatch)
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake(gated_empty=True))

    source = ig.instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_totals"])
    assert len(rows) == 3
    assert rows[0]["follows_and_unfollows"] is None
    assert rows[0]["profile_links_taps"] is None
    # Real zero is preserved when the API returns it (not this fake), while the
    # follow_type breakdown dimensions still arrive for the current window.
    follow_rows = [b for b in rows[0]["breakdowns"] if b["breakdown"] == "follow_type"]
    assert {b["dimension_value"] for b in follow_rows} == set(FOLLOW_DIMS)


def test_insights_totals_resource_declares_gated_column_hints():
    """WU5 gate regression: dlt must materialize fully-NULL gated columns.

    dlt 1.29 skips columns that receive no data at all (the live client
    ``follows_and_unfollows`` is empty even when eligible, obs #537 #5): without
    explicit ``columns=`` hints the raw table lacks the gated columns and the
    totals staging model (``::bigint`` casts) fails against the real DB. The
    hints declare them bigint-nullable so replace always creates them.
    """
    source = ig.instagram_source("test_biz_456", "mock_token")
    totals = source.resources["insights_totals"]
    hints = {name: col for name, col in (totals.columns or {}).items()}
    for metric in ig.TOTAL_VALUE_GATED_METRICS:
        assert metric in hints, f"gated column {metric} needs a dlt bigint-nullable hint"
        assert hints[metric]["data_type"] == "bigint"
        assert hints[metric]["nullable"] is True


def test_insights_totals_breakdown_child_rows_per_window(monkeypatch):
    """IG2 Scenario 2.2: media_product_type breakdown rows ride every window
    row via dedicated calls; follow_type only on the current window; metrics
    without a confirmed breakdown are never requested with one."""
    _freeze_now(monkeypatch)
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake())

    source = ig.instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_totals"])

    for row, offset in zip(rows, (0, 30, 60)):
        entries = row["breakdowns"]
        assert entries, "window row must carry its breakdown child entries"
        for entry in entries:
            assert set(entry) == {"metric", "breakdown", "dimension_value", "value"}
        # accounts_engaged/replies/reposts declare no media_product_type
        # breakdown (probe obs #537) and never appear as breakdown rows.
        assert not any(e["metric"] in ("accounts_engaged", "replies", "reposts") for e in entries)
        mpt = [e for e in entries if e["breakdown"] == "media_product_type"]
        views_dims = [e["dimension_value"] for e in mpt if e["metric"] == "views"]
        assert views_dims == MEDIA_DIMS["views"]
        # Values are the window's own: distinct per window offset.
        if offset == 0:
            assert all(e["value"] <= 10_000 + len(MEDIA_DIMS["views"]) for e in mpt)
        else:
            assert any(e["value"] > 10_000 for e in mpt)
        ft = [e for e in entries if e["breakdown"] == "follow_type"]
        if offset == 0:
            assert {e["dimension_value"] for e in ft} == set(FOLLOW_DIMS)
        else:
            assert ft == [], "follow_type only exists in the current window"


def test_insights_totals_breakdown_error_100_disables_axis_only(monkeypatch):
    """D4 runtime defense: error 100 on a breakdown call disables that axis for
    the run (later windows skip it) without aborting the resource or the
    other axes (follow_type keeps working)."""
    _freeze_now(monkeypatch)
    record: list[dict[str, Any]] = []
    monkeypatch.setattr(
        f"{MODULE}.requests.get",
        _totals_fake(record=record, breakdown_errors={(30, "media_product_type")}),
    )

    source = ig.instagram_source("test_biz_456", "mock_token")
    rows = list(source.resources["insights_totals"])

    assert len(rows) == 3
    # The offset-0 row kept its breakdowns; the axis was turned off afterwards.
    assert rows[0]["breakdowns"]
    assert rows[1]["breakdowns"] == []
    assert rows[2]["breakdowns"] == []
    mpt_calls = [_offset_days(p) for p in record if p.get("breakdown") == "media_product_type"]
    assert mpt_calls == [0, 30], "media_product_type disabled after the error"
    assert any(p.get("breakdown") == "follow_type" for p in record)


def test_insights_totals_common_error_100_aborts(monkeypatch):
    """D4 parity (probe test parity): error 100 on a common/gated batch would
    corrupt a window row, so it aborts instead of degrading."""
    _freeze_now(monkeypatch)
    monkeypatch.setattr(f"{MODULE}.requests.get", _totals_fake(common_error_offsets={0}))

    source = ig.instagram_source("test_biz_456", "mock_token")
    with pytest.raises(Exception, match="Invalid parameter"):
        list(source.resources["insights_totals"])


# ─── Source ordering (IG7-R1 additive-first) ────────────────────────────────


def test_source_resource_order_totals_before_daily(monkeypatch):
    """IG7-R1: the totals resource precedes insights_daily in the source list so
    the additive table is created before the destructive daily shrink."""
    monkeypatch.setattr(f"{MODULE}.requests.get", _daily_fake())

    source = ig.instagram_source("test_biz_456", "mock_token")
    assert list(source.resources) == [
        "media",
        "insights_totals",
        "insights_daily",
        "business_profile",
    ]


# ─── Unchanged healthy behavior (media / profile guard / template contract) ─


def test_media_success(monkeypatch):
    media_response = {
        "data": [
            {
                "id": "media_1",
                "caption": "Nice pic",
                "media_type": "IMAGE",
                "like_count": 42,
                "comments_count": 7,
                "timestamp": "2024-01-01T12:00:00+0000",
                "permalink": "https://instagram.com/p/media_1",
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(media_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    source = ig.instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    assert items[0]["media_id"] == "media_1"
    assert items[0]["caption"] == "Nice pic"
    assert items[0]["media_type"] == "IMAGE"
    assert items[0]["like_count"] == 42
    assert items[0]["comments_count"] == 7
    assert items[0]["permalink"] == "https://instagram.com/p/media_1"
    assert items[0]["timestamp"] == "2024-01-01T12:00:00+0000"


def test_rate_limit(monkeypatch):
    calls = []

    def mock_get(url, params=None):
        calls.append(1)
        if len(calls) == 1:
            return _mock_json({"error": {"code": 4, "message": "Rate limit"}})
        return _mock_json(
            {
                "data": [
                    {
                        "id": "media_1",
                        "media_type": "IMAGE",
                        "like_count": 0,
                        "comments_count": 0,
                    }
                ],
                "paging": {"next": None},
            }
        )

    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)
    monkeypatch.setattr(f"{MODULE}.time.sleep", lambda s: None)

    source = ig.instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    assert len(calls) == 2


def test_token_expired(monkeypatch):
    error_data = {"error": {"code": 190, "message": "Token expired"}}
    monkeypatch.setattr(f"{MODULE}.requests.get", lambda url, params=None: _mock_json(error_data))

    source = ig.instagram_source("test_biz_456", "mock_token")
    with pytest.raises(Exception, match="Token expired"):
        list(source.resources["media"])


def test_media_media_url_and_new_fields(monkeypatch):
    """RED: media_url MUST be persisted + 6 new fields in yielded dict."""
    media_response = {
        "data": [
            {
                "id": "media_u1",
                "caption": "With URL",
                "media_type": "IMAGE",
                "media_url": "https://ig.com/p/media_u1/img.jpg",
                "permalink": "https://instagram.com/p/media_u1",
                "like_count": 10,
                "comments_count": 3,
                "timestamp": "2024-01-01T12:00:00+0000",
                "thumbnail_url": "https://ig.com/p/media_u1/thumb.jpg",
                "shortcode": "ABC123",
                "media_product_type": "FEED",
                "is_comment_enabled": True,
                "owner": {"id": "owner_456"},
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(media_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    source = ig.instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    item = items[0]
    # The core fix — media_url was previously dropped
    assert item["media_url"] == "https://ig.com/p/media_u1/img.jpg"
    # New fields
    assert item["thumbnail_url"] == "https://ig.com/p/media_u1/thumb.jpg"
    assert item["shortcode"] == "ABC123"
    assert item["media_product_type"] == "FEED"
    assert item["owner_id"] == "owner_456"
    assert item["is_comment_enabled"] is True


def test_media_media_url_nullable_fields(monkeypatch):
    """TRIANGULATE: Nullable fields (caption=null, video_title=null) still work."""
    media_response = {
        "data": [
            {
                "id": "media_n1",
                "caption": None,
                "media_type": "VIDEO",
                "media_url": "https://ig.com/p/media_n1/vid.mp4",
                "permalink": "https://instagram.com/p/media_n1",
                "like_count": 5,
                "comments_count": 0,
                "timestamp": "2024-06-15T12:00:00+0000",
                "thumbnail_url": "https://ig.com/p/media_n1/thumb.jpg",
                "shortcode": "DEF456",
                "media_product_type": "REELS",
                "is_comment_enabled": False,
                "owner": {"id": "owner_789"},
            }
        ],
        "paging": {"next": None},
    }

    mock_get = lambda url, params=None: _mock_json(media_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    source = ig.instagram_source("test_biz_456", "mock_token")
    items = list(source.resources["media"])

    assert len(items) == 1
    item = items[0]
    assert item["caption"] is None
    assert item["media_url"] == "https://ig.com/p/media_n1/vid.mp4"
    assert item["thumbnail_url"] == "https://ig.com/p/media_n1/thumb.jpg"
    assert item["shortcode"] == "DEF456"
    assert item["media_product_type"] == "REELS"
    assert item["owner_id"] == "owner_789"
    assert item["is_comment_enabled"] is False


def test_business_profile_daily_guard_skips_same_day(monkeypatch):
    """RED: Business profile daily guard skips 2nd same-day call."""
    today = NOW.date().isoformat()

    class FakeDltCurrent:
        _state = {"last_run": today}

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "ig_biz_456",
        "username": "testbiz",
        "name": "Test Business",
        "profile_picture_url": "https://ig.com/pic.jpg",
        "biography": "A test business",
        "website": "https://testbiz.com",
        "followers_count": 5000,
        "follows_count": 100,
        "media_count": 200,
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    resource = ig.get_business_profile("ig_biz_456", "mock_token")
    results = list(resource)

    assert len(results) == 0, "Expected no yield when already fetched today"


def test_business_profile_daily_guard_fetches_new_day(monkeypatch):
    """TRIANGULATE: Business profile yields data when last_run is yesterday."""

    class FakeDltCurrent:
        _state = {"last_run": "2026-07-27"}  # yesterday

        def resource_state(self):
            return self._state

    monkeypatch.setattr(f"{MODULE}.dlt.current", FakeDltCurrent())

    profile_response = {
        "id": "ig_biz_456",
        "username": "testbiz",
        "name": "Test Business",
        "profile_picture_url": "https://ig.com/pic.jpg",
        "biography": "A test business",
        "website": "https://testbiz.com",
        "followers_count": 5000,
        "follows_count": 100,
        "media_count": 200,
    }

    mock_get = lambda url, params=None: _mock_json(profile_response)  # noqa: E731
    monkeypatch.setattr(f"{MODULE}.requests.get", mock_get)

    resource = ig.get_business_profile("ig_biz_456", "mock_token")
    results = list(resource)

    assert len(results) == 1
    profile = results[0]
    assert profile["ig_id"] == "ig_biz_456"
    assert profile["username"] == "testbiz"
    assert profile["name"] == "Test Business"
    assert profile["profile_picture_url"] == "https://ig.com/pic.jpg"
    assert profile["biography"] == "A test business"
    assert profile["website"] == "https://testbiz.com"
    assert profile["followers_count"] == 5000
    assert profile["follows_count"] == 100
    assert profile["media_count"] == 200


def test_connector_key_and_field():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    ig_cfg = template["connectors"]["instagram"]
    assert "instagram_business_id" in ig_cfg
    assert "token_env" in ig_cfg


def test_connector_insights_days_back():
    template_path = os.path.join(os.path.dirname(__file__), "..", "clients", "_template.yml")
    with open(template_path) as f:
        template = yaml.safe_load(f)

    ig_cfg = template["connectors"]["instagram"]
    assert "insights_days_back" in ig_cfg
    assert isinstance(ig_cfg["insights_days_back"], int)
