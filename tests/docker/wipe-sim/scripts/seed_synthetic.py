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

WU4 archive live fixtures (SDD-D, spec ARC/TEN/CATCH) extend the same pattern
to the connectors whose mains run the post-run append-only archiver:

* ``--connector instagram --corrida K``  -> ``insights_totals`` (window W +
  current CUR, each with nested ``breakdowns`` children) + ``insights_daily``
  (follower series OLDER + RECENT) under ``raw_instagram_<client>``.
* ``--connector instagram --corrida K+1`` -> same tables REPLACED with the
  horizon after it moved: CUR totals only, RECENT follower only — window W is
  evicted from the live tables (spec ARC-S3: W survives only in ``_history``).
* acme follower fixtures carry an extra mid day 2026-08-05 (E ORG-S4 fixture,
  design D5): corrida K answers follower (bias*10+5 -> archived), corrida K+1
  replaces it with the NULL no-data marker (reach present) so the organic
  daily mart must read the archived value on the same date.
* ``--connector tiktok_organic --corrida K|K+1`` -> one ``profile_stats`` row
  per corrida under ``raw_tiktok_organic_<client>`` (each corrida replaces the
  previous row, ARC-R6: prior profile rows are only kept by the archive).

``instagram`` WITHOUT ``--corrida`` keeps the byte-identical WU10 narrow
freeze fixture (``insights_daily`` only, design D8). Fixture getters
(``ig_totals_corrida`` / ``ig_follower_corrida`` / ``profile_stats_corrida``)
are the SINGLE source for both the live dlt loads here and the captured
payloads consumed by ``archive_synthetic.py`` (the SDD-D glue replica), so the
simulated fetch state and the archived payloads cannot drift. Calendar windows
are shared by both tenants (real horizon); metric VALUES and dates are
client-distinct so a cross-tenant leak is detectable in SQL.

Usage (inside the isolated pipeline container, env already wired):

    python /app/wipesim/seed_synthetic.py --client acme
    python /app/wipesim/seed_synthetic.py --client acme --connector meta   # rerun
    python /app/wipesim/seed_synthetic.py --client acme --connector instagram --corrida K
    python /app/wipesim/seed_synthetic.py --client acme --connector tiktok_organic --corrida K+1
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

# ─── WU4 archive fixtures (SDD-D WU4; docker-only) ──────────────────────────
# Two consecutive dlt corridas per client model the servable-horizon drift
# that motivates the archive (spec ARC-S3): corrida K still serves the older
# totals window W plus the current window CUR; corrida K+1 replaces the live
# tables with the moved horizon (CUR only) — W is evicted from LIVE, and only
# the ``_history`` archive keeps it. Every value/date below is deterministic
# and client-distinct so assertions are stable and cross-tenant leaks
# detectable. The corrida getters are imported by archive_synthetic.py so the
# live dlt state and the archived capture payloads always agree.
CORRIDAS: tuple[str, ...] = ("K", "K+1")

IG_WINDOW_W = {"date_start": dt.date(2026, 6, 1), "date_end": dt.date(2026, 6, 30)}
IG_WINDOW_CUR = {"date_start": dt.date(2026, 8, 1), "date_end": dt.date(2026, 8, 31)}

# Metric surface mirror of run_instagram.py:164-182 (single source of truth is
# the connector; the archiver columns are injected from there by the glue).
TOTALS_COMMON_METRICS: tuple[str, ...] = (
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
TOTALS_GATED_METRICS: tuple[str, ...] = ("follows_and_unfollows", "profile_links_taps")
CLIENT_VALUE_BIAS: dict[str, int] = {"acme": 1000, "nike": 2000}

IG_FOLLOWER_OLDER_DATES: dict[str, tuple[dt.date, ...]] = {
    "acme": (dt.date(2026, 6, 11), dt.date(2026, 6, 12)),
    "nike": (dt.date(2026, 6, 21), dt.date(2026, 6, 22)),
}
# E (design D5): acme-only mid day 2026-08-05. It sits INSIDE the K+1 live
# horizon and is served by BOTH corridas: corrida K answers follower_count
# (bias*10 + 5 = 10005 -> archived), corrida K+1 carries the NULL no-data
# marker (reach present) so the organic daily mart COALESCE must read the
# archived value on the same report_date (spec ORG-S4). The date is >=
# 2026-08-01, keeping D's ARC-S3 live-eviction assert (< 08-01 == 0) green.
# nike fixtures stay untouched (nike: empty tuple).
IG_FOLLOWER_MID_DATES: dict[str, tuple[dt.date, ...]] = {
    "acme": (dt.date(2026, 8, 5),),
    "nike": (),
}
IG_FOLLOWER_RECENT_DATES: dict[str, tuple[dt.date, ...]] = {
    "acme": (dt.date(2026, 8, 11), dt.date(2026, 8, 12), dt.date(2026, 8, 13)),
    "nike": (dt.date(2026, 8, 21), dt.date(2026, 8, 22), dt.date(2026, 8, 23)),
}
PROFILE_DATES: dict[str, dict[str, dt.date]] = {
    "acme": {"K": dt.date(2026, 9, 1), "K+1": dt.date(2026, 9, 2)},
    "nike": {"K": dt.date(2026, 9, 3), "K+1": dt.date(2026, 9, 4)},
}


def _ig_totals_row(client_id: str, window: dict[str, dt.date], window_index: int) -> dict:
    """One synthetic ``insights_totals`` row for a horizon window.

    Common metrics always answer (value = client bias + window offset);
    gated metrics answer ONLY in the current window (``window_index == 1``,
    IG3-R1 mirror) and stay NULL elsewhere — never invented (IG3-R2). Nested
    ``breakdowns`` travel inside the payload exactly as the real capture holds
    them (design G2), so the child history keys rebuild from the parent row.
    """
    bias = CLIENT_VALUE_BIAS[client_id]
    row: dict[str, Any] = {
        "date_start": window["date_start"],
        "date_end": window["date_end"],
    }
    for idx, metric in enumerate(TOTALS_COMMON_METRICS):
        row[metric] = bias + window_index * 100 + idx
    # Both gated metrics answer in the current window (IG3-R1 mirror) and stay
    # NULL elsewhere. profile_links_taps MUST answer at least in the current
    # window or dlt never creates the live column (all-NULL values infer no
    # column), which breaks stg_instagram__insights_totals — first executed in
    # the sim by the E organic gate (WU-E4).
    row["follows_and_unfollows"] = bias + window_index if window_index == 1 else None
    row["profile_links_taps"] = bias + window_index if window_index == 1 else None
    row["breakdowns"] = [
        {
            "metric": "views",
            "breakdown": "media_product_type",
            "dimension_value": f"{client_id}-{kind}",
            "value": bias + window_index * 100 + idx,
        }
        for idx, kind in enumerate(("AD", "REEL"))
    ]
    return row


def ig_totals_corrida(client_id: str, corrida: str) -> list[dict[str, Any]]:
    """Totals rows the run K (window W + CUR) or K+1 (CUR only) serves."""
    current = _ig_totals_row(client_id, IG_WINDOW_CUR, window_index=1)
    if corrida == "K":
        return [_ig_totals_row(client_id, IG_WINDOW_W, window_index=0), current]
    return [current]


def _follower_row(client_id: str, report_date: dt.date, no_data: bool = False) -> dict[str, Any]:
    """One ``insights_daily`` row. ``no_data`` marks the E ORG-S4 fixture: the
    live follower is the NULL no-data marker (never 0) while ``reach`` stays
    present, so the mart COALESCE must fall through to the archived value."""
    bias = CLIENT_VALUE_BIAS[client_id]
    return {
        "report_date": report_date,
        "reach": bias + report_date.day,
        "follower_count": None if no_data else bias * 10 + report_date.day,
    }


def ig_follower_corrida(client_id: str, corrida: str) -> list[dict[str, Any]]:
    """Follower series the run serves (trailing-window drift, probe obs #537):
    * K   — OLDER + MID + RECENT days; every day answers follower_count.
    * K+1 — MID days carry the follower NULL no-data marker (reach present, E
      ORG-S4) + RECENT days only (the API stops serving the older rows; the
      archiver flattener drops NULL-follower entries, so the archive keeps the
      corrida-K value on the mid day).
    """
    mid = IG_FOLLOWER_MID_DATES[client_id]
    recent = IG_FOLLOWER_RECENT_DATES[client_id]
    if corrida == "K":
        older = IG_FOLLOWER_OLDER_DATES[client_id]
        return [_follower_row(client_id, day) for day in (*older, *mid, *recent)]
    return [_follower_row(client_id, day, no_data=day in mid) for day in (*mid, *recent)]


def _profile_row(client_id: str, corrida: str) -> dict[str, Any]:
    bias = CLIENT_VALUE_BIAS[client_id]
    day = PROFILE_DATES[client_id][corrida]
    return {
        "report_date": day,
        "follower_count": bias + day.day,
        "following_count": bias // 2 + day.day,
        "likes_count": bias * 3 + day.day,
        "video_count": 50 + day.day,
    }


def profile_stats_corrida(client_id: str, corrida: str) -> list[dict[str, Any]]:
    """The single ``profile_stats`` row a corrida serves (per-run replace)."""
    return [_profile_row(client_id, corrida)]


def _prefix(client_id: str, kind: str, i: int) -> str:
    return f"{client_id}-{kind}-{i:03d}"


def _ad_status(connector: str, i: int) -> str:
    """Status vocabulary per connector, mirroring each staging contract.

    Meta/TikTok staging accepts ACTIVE/PAUSED/ARCHIVED/DELETED; Google Ads
    (run_google.py passthrough of the Ads API) only ENABLED/PAUSED/REMOVED.
    The shared row builders must emit the vocabulary the target staging
    contract accepts (SDD-F WU5 gate finding: google staging schema tests
    never ran in the sim before the nightly union test exposed ACTIVE rows
    failing ``accepted_values_stg_google__ads/campaigns_status``).
    """
    if connector == "google":
        return ("ENABLED", "PAUSED", "REMOVED")[i % 3]
    return "ACTIVE" if i % 2 == 0 else "PAUSED"


def _row_ad(client_id: str, connector: str, i: int) -> dict[str, Any]:
    """One synthetic ads row covering every column stg_<conn>__ads selects."""
    return {
        "ad_id": _prefix(client_id, f"{connector}-ad", i),
        "ad_group_id": _prefix(client_id, f"{connector}-ag", i),
        "campaign_id": _prefix(client_id, f"{connector}-cmp", i),
        "ad_name": f"{client_id} {connector} ad {i}",
        "status": _ad_status(connector, i),
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
        "status": _ad_status(connector, i),
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


def build_source(client_id: str, connector: str, corrida: str | None = None) -> list[Any]:
    """One dlt source per (client, connector): ads + campaigns, replace.

    ``instagram`` without ``corrida`` keeps the WU10 narrow freeze fixture
    (``insights_daily`` only, design D8). With ``--corrida`` it returns the
    WU4 archive live tables (``insights_totals`` + wide ``insights_daily``);
    ``tiktok_organic`` returns the ``profile_stats`` live row of the corrida.
    """
    n = COUNTS.get(client_id, 2)
    if connector == "instagram":
        if corrida is None:
            # Freeze fixture: only the narrow daily table is needed.
            return [_resource("insights_daily", list(_rows_instagram_daily(client_id, n)))]
        return [
            _resource("insights_totals", ig_totals_corrida(client_id, corrida)),
            _resource("insights_daily", ig_follower_corrida(client_id, corrida)),
        ]
    if connector == "tiktok_organic":
        return [_resource("profile_stats", profile_stats_corrida(client_id, corrida or "K"))]
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
        "(freeze/archive fixtures); default = YAML enabled connectors",
    )
    parser.add_argument(
        "--corrida",
        choices=CORRIDAS,
        default=None,
        help="WU4 archive live state to load (replace): K = window W + current "
        "horizon; K+1 = moved horizon without W (ARC-S3). Only used by the "
        "archive connectors (instagram/tiktok_organic); instagram without it "
        "keeps the narrow freeze fixture",
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
        info = pipeline.run(build_source(args.client, connector, args.corrida))
        print(f"[seed] {args.client}/{connector}: {info}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
