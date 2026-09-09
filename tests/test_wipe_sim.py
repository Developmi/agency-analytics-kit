"""WU10 wipe-sim — docker-marked end-to-end gate for multi-tenancy-real.

Spec G-S2 / design D10 / tasks obs #563 WU10. Runs ONLY when ``WIPE_SIM=1``
(the normal suite stays at its baseline count — module-level skip otherwise).
Drives a fully ISOLATED compose project (``wipesim_mt``: own network, own
volume, own service/container names, image rebuilt from the repo Dockerfile
with ``--no-cache``) and asserts the runtime scenarios that no offline
substitute can prove (spec A/B/C/D/G + NFR-4):

* A-S1/A-S2/A-S3 — synthetic dlt loads (replace) land only in the client's own
  ``raw_<conn>_<client>`` namespace; a cross-tenant re-run leaves the other
  tenant byte-identical; a same-tenant re-run is idempotent.
* A-S4 + D-S1/D-S2 — init 02 v2: with the env-gate OFF no ``metabase_reader``
  role exists; re-running with ON creates role + catch-up USAGE/SELECT grants +
  global default privileges idempotently; the reader can SELECT tenant objects
  but cannot INSERT/TRUNCATE them.
* B-S1/B-S3 — per-client ``dbt run`` (real pipeline.sh select via
  ``pipeline_plan``) builds ONLY ``client_acme`` / ``client_nike`` and each
  staging model reads its own raw namespace.
* B-S2/B-R5 — ``DROP SCHEMA client_nike CASCADE`` (plus raw drop) removes nike
  and leaves acme intact and queryable.
* C-S1 — shared monitoring chain stays client-partitioned in staging/public.
* G-S1 — an active client with zero enabled connectors creates no raw schema
  and nothing fails (chain-only dbt run).
* NFR-4 — catalog benchmark: exact schema set for 2 clients x 3 overlapping
  connectors; no legacy shared ``raw_*`` appears.
* freeze-regression DB gate (design D8): ``check_table_via_db`` with
  ``client_id`` resolves ``raw_instagram_acme`` and reports real freeze state.
* SDD-D archive gate (WU4, obs #602): the append-only ``_history`` archiver is
  exercised on the real sim postgres through its own replica of the connector
  glue (archive_synthetic.py) fed by the same corrida fixtures that seed the
  live tables (seed_synthetic.py):
  * CATCH-S1/ARC-S2/S2b — a simulated mid-archive crash leaves a subset of
    natural keys archived; the full retry completes the missing follower keys
    and does NOT duplicate totals/child rows (DO NOTHING keeps ``captured_at``).
  * ARC-S3 — corrida K archives window W; corrida K+1 replaces the live
    tables without W: W is evicted from ``insights_totals`` yet survives in
    ``insights_totals_history`` with its original ``captured_at``.
  * TEN-S1 — acme + nike IG/TikTok archive rows land only in each tenant's own
    ``raw_instagram_<c>``/``raw_tiktok_organic_<c>``; nike's replace+archive
    re-run leaves acme's archive tables byte-identical (NFR-4-type assert).
  * TEN-S2 — tables created AFTER the RBAC gate is enabled are SELECTable by
    ``metabase_reader`` without re-running init 02 (global default privileges,
    design gate G3 evidence).
  * cp_nfr4 catalog extends by the four ``_history``-hosting raw schemas
    (raw_instagram_acme/nike, raw_tiktok_organic_acme/nike).
* SDD-E organic gate (WU-E4, spec E VER-R1/ORG/NOM-E/HIG): after the acme
  archive story the isolated stack runs REAL ``dbt run`` (organic staging +
  3 ``organic_*`` marts), the repo's first automated ``dbt test``, and ``dbt
  ls`` over acme — asserting ORG-S1..S7 (evicted window from history, no daily
  fan-out, gated NULL, follower>30d COALESCE, history-only reach NULL, TT
  multi-day, duplicate-key guard), NOM-E-S1 (4 ``_history`` sources resolve),
  NOM-E-S2 (breakdowns source unconsumed) and HIG-S1 (no real client id).

The user's real stack (agency_postgres/agency_pipeline/db_pgdata/agency_*_net)
is NEVER started, stopped, written or recreated: it is snapshotted before and
after (read-only ``docker ps -a`` / volume / network checks) and the sim runs
on its own project. Teardown removes every wipesim container/volume/network/
image (``down -v`` + ``rmi``) and asserts zero residual docker objects.

Run: ``WIPE_SIM=1 uv run pytest tests/test_wipe_sim.py -v`` (host needs a live
docker daemon with build capability; postgres:16-alpine + pinned python bases
are pulled from the registry).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

WIPE_SIM = os.environ.get("WIPE_SIM") == "1"
pytestmark = pytest.mark.skipif(not WIPE_SIM, reason="wipe-sim docker-marked; set WIPE_SIM=1")

COMPOSE = Path(__file__).resolve().parent / "docker" / "wipe-sim" / "compose.yaml"
PROJECT = "wipesim_mt"
IMAGE = "agency-pipeline-wipesim-mt:latest"

DB_USER = "agency_admin"
DB_PASS = "wipesim_mt_secret"
DB_NAME = "agency_dw"
READER_PASS = "wipesim_reader"


def run(args: list[str], check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run a host command and return the completed process."""
    proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if check and proc.returncode != 0:
        raise AssertionError(
            f"command failed ({proc.returncode}): {' '.join(args)}\n"
            f"stdout: {proc.stdout[-4000:]}\nstderr: {proc.stderr[-4000:]}"
        )
    return proc


def compose(*args: str, check: bool = True, timeout: int = 1800) -> subprocess.CompletedProcess:
    """Run ``docker compose -f <wipe-sim compose> [-p wipesim_mt] <args>``."""
    return run(
        ["docker", "compose", "-f", str(COMPOSE), "-p", PROJECT, *args],
        check=check,
        timeout=timeout,
    )


def psql(sql: str, *, user: str = DB_USER, password: str = DB_PASS, check: bool = True) -> str:
    """Run a SQL statement in the sim postgres and return stdout (trimmed)."""
    proc = compose(
        "exec",
        "-T",
        "-e",
        f"PGPASSWORD={password}",
        "postgres",
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        user,
        "-d",
        DB_NAME,
        "-Atc",
        sql,
        check=check,
    )
    return proc.stdout.strip()


def psql_rows(sql: str, *, user: str = DB_USER, password: str = DB_PASS) -> list[str]:
    out = psql(sql, user=user, password=password)
    return [line for line in out.splitlines() if line]


def reader_psql(sql: str, *, check: bool = True) -> subprocess.CompletedProcess:
    """Run SQL as ``metabase_reader`` (returns the process for rc/stderr checks)."""
    return compose(
        "exec",
        "-T",
        "-e",
        f"PGPASSWORD={READER_PASS}",
        "postgres",
        "psql",
        "-h",
        "127.0.0.1",
        "-U",
        "metabase_reader",
        "-d",
        DB_NAME,
        "-Atc",
        sql,
        check=check,
    )


def exec_pipeline(
    workdir: str, cmd: list[str], *, check: bool = True, timeout: int = 1800
) -> subprocess.CompletedProcess:
    return compose("exec", "-T", "-w", workdir, "pipeline", *cmd, check=check, timeout=timeout)


def schemas() -> set[str]:
    return {
        row
        for row in psql_rows(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT LIKE 'pg\\_%' AND schema_name <> 'information_schema'"
        )
    }


def dbt_plan_models() -> str:
    """Replicate pipeline.sh: plan select + investment marts when all 3."""
    code = (
        "from agency_analytics.pipeline_plan import build_plan, INVESTMENT_MARTS;"
        "p = build_plan(['meta','tiktok','google']);"
        "sel = list(p.models) + (list(INVESTMENT_MARTS) if p.investment else []);"
        "print(' '.join(sel))"
    )
    return exec_pipeline("/app/src", ["python", "-c", code]).stdout.strip()


def snapshot_real_stack() -> dict[str, object]:
    """Read-only snapshot of the user's real docker state."""
    return {
        "ps": run(["docker", "ps", "-a", "--format", "{{.Names}}|{{.Status}}"]).stdout,
        "vols": run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout,
        "nets": run(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout,
    }


def assert_real_stack_unchanged(before: dict[str, object], label: str) -> None:
    """The user's real docker state must be untouched; wipesim entries excluded."""
    after = snapshot_real_stack()
    for key in ("ps", "vols", "nets"):
        before_lines = [ln for ln in before[key].splitlines() if PROJECT not in ln]
        after_lines = [ln for ln in after[key].splitlines() if PROJECT not in ln]
        assert after_lines == before_lines, (
            f"{label}: real docker {key} changed!\nbefore: {before_lines}\nafter: {after_lines}"
        )


@pytest.fixture(scope="module")
def sim_stack():
    """Build + boot the isolated stack; teardown (down -v + rmi) on exit."""
    before = snapshot_real_stack()
    assert_real_stack_unchanged(before, "pre-build")
    try:
        yield
    finally:
        compose("down", "-v", "--remove-orphans", check=False)
        run(["docker", "rmi", IMAGE], check=False, timeout=300)
        residual_ps = run(["docker", "ps", "-a", "--format", "{{.Names}}"]).stdout
        residual_vols = run(["docker", "volume", "ls", "--format", "{{.Name}}"]).stdout
        residual_nets = run(["docker", "network", "ls", "--format", "{{.Name}}"]).stdout
        residual_imgs = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).stdout
        for label, out in (
            ("containers", residual_ps),
            ("volumes", residual_vols),
            ("networks", residual_nets),
            ("images", residual_imgs),
        ):
            assert not any(
                line.startswith(PROJECT) or PROJECT in line for line in out.splitlines()
            ), f"residual wipesim {label} after teardown:\n{out}"
        assert IMAGE not in residual_imgs, f"residual wipesim image {IMAGE}"
        assert_real_stack_unchanged(before, "post-teardown")


def seed(client: str, connector: str | None = None) -> None:
    cmd = ["python", "/app/wipesim/seed_synthetic.py", "--client", client]
    if connector:
        cmd += ["--connector", connector]
    exec_pipeline("/app", cmd)


def seed_corrida(client: str, connector: str, corrida: str) -> None:
    """WU4 live state: seed one archive connector at corrida K or K+1."""
    exec_pipeline(
        "/app",
        [
            "python",
            "/app/wipesim/seed_synthetic.py",
            "--client",
            client,
            "--connector",
            connector,
            "--corrida",
            corrida,
        ],
    )


def archive_synthetic(
    client: str, connector: str, corrida: str, skip_follower: bool = False
) -> None:
    """WU4: run the archive glue replica for one (client, connector, corrida)."""
    cmd = [
        "python",
        "/app/wipesim/archive_synthetic.py",
        "--client",
        client,
        "--connector",
        connector,
        "--corrida",
        corrida,
    ]
    if skip_follower:
        cmd.append("--skip-follower")
    exec_pipeline("/app", cmd)


def fingerprint_raw(client: str, conn: str) -> str:
    return psql(
        "SELECT count(*) || ':' || md5(string_agg(ad_id || '|' || spend::text, ','"
        f" ORDER BY ad_id)) FROM raw_{conn}_{client}.ads"
    )


# WU4 archive tables per connector (NOM-R1 names; child keeps the dlt ``__``).
ARCHIVE_TABLES: dict[str, tuple[str, ...]] = {
    "instagram": (
        "insights_totals_history",
        "insights_totals__breakdowns_history",
        "follower_count_history",
    ),
    "tiktok_organic": ("profile_stats_history",),
}

# E organic gate (WU-E4): staging ancestors of the three organic marts that
# the wipe-sim can actually build. build_plan(['instagram','tiktok_organic'])
# also lists stg_instagram__media / stg_tiktok_organic__videos_organic (and
# the monitoring chain), whose raw parents the sim never seeds; these three
# views sit exactly over the seeded archive-connector raw tables.
ORGANIC_MART_STAGING: tuple[str, ...] = (
    "stg_instagram__insights_totals",
    "stg_instagram__insights_daily",
    "stg_tiktok_organic__profile_stats",
)


def fingerprint_archive(client: str, conn: str, table: str) -> str:
    """(count, md5) over a ``_history`` table's rows text-sorted — byte-identity
    fingerprint for the TEN-S1 cross-tenant re-run assert (NFR-4 type)."""
    return psql(
        "SELECT count(*)::text || ':' || "
        "COALESCE(md5(string_agg(x.r, ',' ORDER BY x.r)), 'empty') "
        f"FROM (SELECT t::text AS r FROM raw_{conn}_{client}.{table} t) x"
    )


def archive_fingerprints(client: str) -> dict[str, str]:
    return {
        f"{conn}.{table}": fingerprint_archive(client, conn, table)
        for conn, tables in ARCHIVE_TABLES.items()
        for table in tables
    }


def captured_at_epoch(client: str, conn: str, table: str) -> str:
    """Single run timestamp of a ``_history`` table (min epoch; every row of one
    archive run shares it — ARC-R3). Schema = ``raw_<conn>_<client>``."""
    return psql(
        f"SELECT min(extract(epoch from captured_at))::text FROM raw_{conn}_{client}.{table}"
    )


def run_dbt(
    client_id: str,
    select_models: str,
    tag: str,
    command: str = "run",
    *,
    check: bool = True,
    extra: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    """Run a dbt ``command`` (``run``/``test``/``ls``) against the real
    pipeline select (pipeline.sh contract) with ``client_id`` vars, /tmp
    artifacts. ``select_models`` may be empty (whole-project listing, e.g.
    ``dbt ls --resource-type source``). ``check=False`` returns the process so
    callers can assert a failing dbt exit (ORG-S7)."""
    args = ["dbt", command]
    if select_models:
        args += ["--select", select_models]
    args += [
        "--vars",
        json.dumps({"client_id": client_id}),
        "--profiles-dir",
        ".",
        "--target-path",
        f"/tmp/dbt_target_{tag}",
        "--log-path",
        f"/tmp/dbt_log_{tag}",
        *extra,
    ]
    return exec_pipeline("/app/src/dbt_project", args, check=check)


# Scenario checkpoints --------------------------------------------------------


def cp_a_s1_s2_s3() -> None:
    """A-S1 (isolation), A-S2 (cross-tenant replace), A-S3 (rerun idempotent)."""
    seed("acme")
    seed("nike")
    for conn in ("meta", "tiktok", "google"):
        assert psql(f"SELECT count(*) FROM raw_{conn}_acme.ads WHERE ad_id LIKE 'acme-%'") == "2", (
            f"A-S1 {conn}: expected 2 acme rows"
        )
        assert psql(f"SELECT count(*) FROM raw_{conn}_acme.ads WHERE ad_id LIKE 'nike-%'") == "0", (
            f"A-S1 {conn}: nike rows leaked into acme namespace"
        )
        assert psql(f"SELECT count(*) FROM raw_{conn}_nike.ads WHERE ad_id LIKE 'nike-%'") == "3", (
            f"A-S1 {conn}: expected 3 nike rows"
        )
        assert psql(f"SELECT count(*) FROM raw_{conn}_nike.ads WHERE ad_id LIKE 'acme-%'") == "0", (
            f"A-S1 {conn}: acme rows leaked into nike namespace"
        )
    # A-S2: nike meta re-run (replace) must leave acme byte-identical.
    acme_before = fingerprint_raw("acme", "meta")
    seed("nike", "meta")
    assert fingerprint_raw("acme", "meta") == acme_before, "A-S2: acme changed after nike replace"
    # A-S3: acme meta re-run (replace) is idempotent and leaves nike intact.
    nike_before = fingerprint_raw("nike", "meta")
    seed("acme", "meta")
    assert fingerprint_raw("acme", "meta") == acme_before, "A-S3: acme rerun changed its own state"
    assert fingerprint_raw("nike", "meta") == nike_before, "A-S3: acme rerun touched nike state"


def cp_monitoring_seed() -> None:
    """Insert per-client observability rows as pipeline.sh does (C-R2)."""
    for client in ("acme", "nike"):
        psql(
            "INSERT INTO public.pipeline_runs (client_id, status, started_at, finished_at, "
            "connectors_total, connectors_ok, connectors_failed, dbt_status) VALUES ("
            f"'{client}', 'success', now() - interval '1 day', now() - interval '1 day' "
            f"+ interval '2 minutes', 3, 3, 0, 'success')"
        )


def cp_b_s1_b_s3(client_id: str, models: str, tag: str) -> None:
    """B-S1/B-S3: one per-client dbt run with the real pipeline.sh select."""
    assert models, f"B-S1 {client_id}: empty dbt select"
    run_dbt(client_id, models, tag)
    for obj in (
        "stg_meta__ads",
        "stg_tiktok__ads",
        "stg_google__ads",
        "int_unified_spend",
        "ad_spend_summary",
        "campaign_performance",
    ):
        found = psql(
            "SELECT count(*) FROM information_schema.tables "
            f"WHERE table_schema = 'client_{client_id}' AND table_name = '{obj}'"
        )
        assert found == "1", f"B-S1 {client_id}: {obj} missing in client_{client_id}"
    # B-S3: the staging view reads the client's own raw namespace.
    raw_n = int(psql(f"SELECT count(*) FROM raw_meta_{client_id}.ads"))
    stg_n = int(psql(f"SELECT count(*) FROM client_{client_id}.stg_meta__ads"))
    assert stg_n == raw_n, f"B-S3 {client_id}: stg_meta__ads count {stg_n} != raw count {raw_n}"
    # Marts stamp the running client_id (spec G2 / D3).
    stamp = psql_rows(f"SELECT DISTINCT client_id FROM client_{client_id}.ad_spend_summary")
    assert stamp == [client_id], f"B-S1 {client_id}: mart client_id stamp {stamp}"


def cp_c_s1() -> None:
    """C-S1: shared monitoring chain shows both clients under their client_id."""
    runs = psql_rows(
        "SELECT client_id || ':' || run_status FROM staging.stg_public__pipeline_runs"
        " ORDER BY client_id"
    )
    assert runs == ["acme:success", "nike:success"], f"C-S1: shared runs rows: {runs}"
    daily = psql_rows(
        "SELECT client_id || ':' || total_runs::text FROM staging.int_pipeline_daily_summary "
        "ORDER BY client_id"
    )
    assert daily == ["acme:1", "nike:1"], f"C-S1: daily summary rows: {daily}"
    mon = psql_rows(
        "SELECT client_id || ':' || run_status FROM public.pipeline_monitoring ORDER BY client_id"
    )
    assert mon == ["acme:success", "nike:success"], f"C-S1: pipeline_monitoring rows: {mon}"


def cp_d_gate_off_on() -> None:
    """D-S1 (ON branch) + A-S4 reader boundary + D-S2 idempotent re-run."""
    compose(
        "exec",
        "-T",
        "-e",
        "METABASE_READER_ENABLED=true",
        "-e",
        f"METABASE_READER_PASSWORD={READER_PASS}",
        "postgres",
        "/docker-entrypoint-initdb.d/02-bootstrap-rbac.sh",
    )
    role = psql_rows("SELECT rolname FROM pg_roles WHERE rolname = 'metabase_reader'")
    assert role == ["metabase_reader"], f"D-S1 ON: role missing: {role}"
    grants = psql_rows(
        "SELECT table_schema FROM information_schema.role_table_grants "
        "WHERE grantee = 'metabase_reader' AND privilege_type = 'SELECT' "
        "GROUP BY table_schema ORDER BY table_schema"
    )
    for schema in (
        "client_acme",
        "client_nike",
        "raw_meta_acme",
        "raw_meta_nike",
        "staging",
        "public",
    ):
        assert schema in grants, f"D-S1 ON: missing SELECT grant on {schema}: {grants}"
    adp = psql_rows(
        "SELECT defaclobjtype FROM pg_default_acl WHERE defaclacl::text LIKE '%metabase_reader%'"
    )
    assert len(adp) >= 2, f"D-S1 ON: expected global ADP rows, got {adp}"
    # A-S4: reader SELECT works on tenant objects; writes are denied.
    ok = reader_psql("SELECT count(*) FROM client_acme.ad_spend_summary")
    assert ok.returncode == 0 and ok.stdout.strip().isdigit(), "A-S4: reader SELECT failed"
    denied = reader_psql(
        "INSERT INTO client_acme.ad_spend_summary (client_id, platform, report_date) "
        "VALUES ('x','x','2026-09-01')",
        check=False,
    )
    assert denied.returncode != 0 and "permission denied" in (denied.stderr + denied.stdout), (
        f"A-S4: reader INSERT was not denied: {denied.stdout} {denied.stderr}"
    )
    trunc = reader_psql("TRUNCATE TABLE raw_meta_nike.ads", check=False)
    assert trunc.returncode != 0 and "permission denied" in (trunc.stderr + trunc.stdout), (
        f"A-S4: reader TRUNCATE was not denied: {trunc.stdout} {trunc.stderr}"
    )
    # D-S2: ON re-run idempotent (exit 0, grant count unchanged).
    grant_count = psql_rows(
        "SELECT count(*) FROM information_schema.role_table_grants "
        "WHERE grantee = 'metabase_reader'"
    )
    compose(
        "exec",
        "-T",
        "-e",
        "METABASE_READER_ENABLED=true",
        "-e",
        f"METABASE_READER_PASSWORD={READER_PASS}",
        "postgres",
        "/docker-entrypoint-initdb.d/02-bootstrap-rbac.sh",
    )
    assert (
        psql_rows(
            "SELECT count(*) FROM information_schema.role_table_grants "
            "WHERE grantee = 'metabase_reader'"
        )
        == grant_count
    ), "D-S2: ON re-run duplicated grants"


def cp_b_s2_teardown() -> None:
    """B-S2/B-R5: DROP SCHEMA client_nike CASCADE + raw drops (incl. the WU4
    archive raw schemas) leaves acme intact."""
    psql("DROP SCHEMA IF EXISTS client_nike CASCADE")
    for conn in ("meta", "tiktok", "google", "instagram", "tiktok_organic"):
        psql(f"DROP SCHEMA IF EXISTS raw_{conn}_nike CASCADE")
    assert (
        psql_rows(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE '%nike%'"
        )
        == []
    ), "B-S2: nike schemas still present after teardown"
    acme_rows = int(psql("SELECT count(*) FROM client_acme.ad_spend_summary"))
    assert acme_rows > 0, "B-S2: acme mart not queryable after teardown"
    assert psql("SELECT count(*) FROM raw_meta_acme.ads") == "2", "B-S2: acme raw altered"


def cp_g_s1() -> None:
    """G-S1: zero-connector client (zeta) — chain-only dbt run, no raw schemas."""
    # pipeline.sh runs the monitoring chain even for a zero-connector client.
    run_dbt(
        "zeta",
        "stg_public__pipeline_runs stg_public__pipeline_run_steps "
        "int_pipeline_daily_summary pipeline_monitoring",
        "zeta",
    )
    zeta_raw = psql_rows(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'raw\\_%\\_zeta' OR schema_name = 'client_zeta'"
    )
    assert zeta_raw == [], f"G-S1: zeta schemas created for zero-connector client: {zeta_raw}"


# ─── SDD-D archive checkpoints (WU4; run before cp_nfr4 / RBAC enable) ─────


def cp_archive_live_seed_k() -> None:
    """Seed the WU4 archive live fixtures for acme + nike at corrida K.

    Creates the four ``raw_instagram_<c>`` / ``raw_tiktok_organic_<c>`` schemas
    (TEN-R1) with the corrida-K live tables (totals window W + CUR, follower
    series, profile_stats row). The schemas must exist BEFORE cp_nfr4 (catalog)
    and BEFORE the RBAC gate enable; the ``_history`` tables are created later
    by archive_synthetic runs (TEN-S2 needs at least one post-enable creation).
    """
    for client in ("acme", "nike"):
        for connector in ("instagram", "tiktok_organic"):
            seed_corrida(client, connector, "K")
    for client in ("acme", "nike"):
        for conn in ("instagram",):
            assert psql(f"SELECT count(*) FROM raw_{conn}_{client}.insights_totals") == "2", (
                f"WU4 {client}: corrida K live totals count"
            )
            assert (
                psql(
                    "SELECT count(*) FROM raw_instagram_"
                    f"{client}.insights_totals WHERE date_start = '2026-06-01'"
                )
                == "1"
            ), f"WU4 {client}: window W missing from live totals at corrida K"
            assert (
                psql(f"SELECT count(*) FROM raw_instagram_{client}.insights_totals__breakdowns")
                == "4"
            ), f"WU4 {client}: live breakdown child rows at corrida K"
            # E (D5): acme's live follower series at corrida K gained the mid
            # day 2026-08-05 (older 2 + mid 1 + recent 3 = 6); nike is
            # untouched (older 2 + recent 3 = 5).
            expected_live_follower = "6" if client == "acme" else "5"
            assert psql(f"SELECT count(*) FROM raw_instagram_{client}.insights_daily") == (
                expected_live_follower
            ), f"WU4 {client}: live follower series rows at corrida K"
        assert psql(f"SELECT count(*) FROM raw_tiktok_organic_{client}.profile_stats") == "1", (
            f"WU4 {client}: live profile_stats row at corrida K"
        )


def cp_archive_acme() -> dict[str, str]:
    """Acme archive story: CATCH-S1 subset/retry, ARC-S2/S2b, ARC-S3, TTK rows.

    Runs the glue replica against the corrida-K live state seeded above:
    1. ``--skip-follower`` simulates a mid-archive crash (CATCH-S1 subset):
       totals + child natural keys archived, follower keys missing.
    2. full retry completes the follower keys and does NOT duplicate totals or
       child rows — the original ``captured_at`` is preserved (DO NOTHING).
    3. corrida K+1 replaces the live tables without window W (ARC-S3): W is
       absent from ``insights_totals`` yet still present in the archive with
       its first-run timestamp; child rows dedupe (ARC-S2b).
    4. TikTok profile_stats rows accumulate per corrida (ARC-R6).

    Returns the acme archive fingerprints for the TEN-S1 byte-identity assert.
    """
    # 1) CATCH-S1 partial state: totals + child archived, follower not.
    archive_synthetic("acme", "instagram", "K", skip_follower=True)
    for table, count in (
        ("insights_totals_history", "2"),
        ("insights_totals__breakdowns_history", "4"),
    ):
        assert psql(f"SELECT count(*) FROM raw_instagram_acme.{table}") == count, (
            f"CATCH-S1: acme {table} count after partial archive"
        )
    assert (
        psql(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'raw_instagram_acme' "
            "AND table_name = 'follower_count_history'"
        )
        == "0"
    ), "CATCH-S1: follower table exists although the crash preceded it"
    t1 = captured_at_epoch("acme", "instagram", "insights_totals_history")
    for table in ("insights_totals_history", "insights_totals__breakdowns_history"):
        assert psql("SELECT count(DISTINCT captured_at) FROM raw_instagram_acme." + table) == "1", (
            f"ARC-R3: acme {table} captured_at not run-uniform"
        )

    # 2) CATCH-S1 full retry: missing follower keys archived, no duplicates.
    archive_synthetic("acme", "instagram", "K")
    # E (D5): the acme corrida-K follower set is older(2) + mid(1: 2026-08-05)
    # + recent(3) = 6 archived keys.
    assert psql("SELECT count(*) FROM raw_instagram_acme.follower_count_history") == "6", (
        "CATCH-S1: retry did not complete the follower keys"
    )
    follower_t1 = captured_at_epoch("acme", "instagram", "follower_count_history")
    for table, count in (
        ("insights_totals_history", "2"),
        ("insights_totals__breakdowns_history", "4"),
    ):
        assert psql(f"SELECT count(*) FROM raw_instagram_acme.{table}") == count, (
            f"CATCH-S1: retry duplicated {table} rows"
        )
        assert captured_at_epoch("acme", "instagram", table) == t1, (
            f"CATCH-S1/ARC-S2: retry overwrote {table} first-write captured_at"
        )

    # 3) ARC-S3: corrida K+1 replaces live without W; archive keeps W @T1.
    seed_corrida("acme", "instagram", "K+1")
    assert (
        psql(
            "SELECT count(*) FROM raw_instagram_acme.insights_totals "
            "WHERE date_start = '2026-06-01'"
        )
        == "0"
    ), "ARC-S3: window W still served by live table after corrida K+1"
    assert psql("SELECT count(*) FROM raw_instagram_acme.insights_totals") == "1", (
        "ARC-S3: corrida K+1 live totals count"
    )
    assert (
        psql(
            "SELECT count(*) FROM raw_instagram_acme.insights_daily "
            "WHERE report_date < '2026-08-01'"
        )
        == "0"
    ), "ARC-S3: evicted follower rows still in live insights_daily"
    archive_synthetic("acme", "instagram", "K+1")
    assert (
        psql(
            "SELECT count(*) FROM raw_instagram_acme.insights_totals_history "
            "WHERE date_start = '2026-06-01'"
        )
        == "1"
    ), "ARC-S3: window W lost from insights_totals_history after K+1"
    assert (
        psql("SELECT count(*) FROM raw_instagram_acme.insights_totals__breakdowns_history") == "4"
    ), "ARC-S2b: breakdown child archive duplicated across corridas"
    for table, count in (
        ("insights_totals_history", "2"),
        ("insights_totals__breakdowns_history", "4"),
        # E (D5): acme archived follower keys = 6 (mid day included); the K+1
        # NULL-marker row is dropped by the flattener, so the count holds.
        ("follower_count_history", "6"),
    ):
        assert psql(f"SELECT count(*) FROM raw_instagram_acme.{table}") == count, (
            f"ARC-S2/ARC-S3: {table} row count after corrida K+1"
        )
    for table in ("insights_totals_history", "insights_totals__breakdowns_history"):
        assert captured_at_epoch("acme", "instagram", table) == t1, (
            f"ARC-S3/ARC-S2: {table} captured_at changed after corrida K+1"
        )
    assert captured_at_epoch("acme", "instagram", "follower_count_history") == follower_t1, (
        "ARC-S3/ARC-S2: follower_count_history captured_at changed after corrida K+1"
    )

    # 4) TikTok: profile rows accumulate per corrida (ARC-R6).
    archive_synthetic("acme", "tiktok_organic", "K")
    assert psql("SELECT count(*) FROM raw_tiktok_organic_acme.profile_stats_history") == "1"
    seed_corrida("acme", "tiktok_organic", "K+1")
    assert (
        psql(
            "SELECT count(*) FROM raw_tiktok_organic_acme.profile_stats "
            "WHERE report_date = '2026-09-01'"
        )
        == "0"
    ), "ARC-R6: prior profile row still live after corrida K+1"
    archive_synthetic("acme", "tiktok_organic", "K+1")
    rows = psql_rows(
        "SELECT report_date::text FROM raw_tiktok_organic_acme.profile_stats_history "
        "ORDER BY report_date"
    )
    assert rows == ["2026-09-01", "2026-09-02"], f"ARC-R6: acme profile_stats_history rows: {rows}"
    return archive_fingerprints("acme")


def cp_archive_nike_ten(acme_before: dict[str, str]) -> None:
    """Nike archive story AFTER the RBAC gate enable: TEN-S2 grants + TEN-S1.

    The nike ``_history`` tables are created for the first time HERE, i.e. after
    cp_d_gate_off_on enabled ``metabase_reader`` and set the GLOBAL default
    privileges — so their SELECT coverage can only come from the ADP (design
    gate G3), never from the init catch-up. TEN-S2 asserts the reader can SELECT
    them without re-running init 02. TEN-S1 then re-runs nike (replace + archive
    at corrida K+1) and asserts acme's archive tables stay byte-identical and
    that nike rows live only under nike's own schemas.
    """
    # TEN-S2: first nike archive run happens post-enable (tables created now).
    archive_synthetic("nike", "instagram", "K")
    archive_synthetic("nike", "tiktok_organic", "K")
    for conn, table in (
        ("instagram", "insights_totals_history"),
        ("instagram", "follower_count_history"),
        ("tiktok_organic", "profile_stats_history"),
    ):
        ok = reader_psql(f"SELECT count(*) FROM raw_{conn}_nike.{table}")
        assert ok.returncode == 0 and ok.stdout.strip().isdigit(), (
            f"TEN-S2: metabase_reader SELECT failed on {table}: {ok.stdout} {ok.stderr}"
        )
        assert (
            psql(
                "SELECT count(*) FROM information_schema.role_table_grants "
                "WHERE grantee = 'metabase_reader' AND privilege_type = 'SELECT' "
                f"AND table_schema = 'raw_{conn}_nike' AND table_name = '{table}'"
            )
            == "1"
        ), f"TEN-S2: {table} reader grant missing or duplicated"
    # TEN-S1: nike re-run (replace + archive at corrida K+1) leaves acme intact.
    for connector in ("instagram", "tiktok_organic"):
        seed_corrida("nike", connector, "K+1")
        archive_synthetic("nike", connector, "K+1")
    assert archive_fingerprints("acme") == acme_before, (
        "TEN-S1: nike replace+archive changed acme archive tables"
    )
    assert psql("SELECT count(*) FROM raw_instagram_nike.insights_totals_history") == "2", (
        "TEN-S1: nike totals archive count"
    )
    assert psql("SELECT count(*) FROM raw_instagram_nike.follower_count_history") == "5", (
        "TEN-S1: nike follower archive count"
    )
    assert psql("SELECT count(*) FROM raw_tiktok_organic_nike.profile_stats_history") == "2", (
        "TEN-S1: nike profile archive count"
    )
    # Client bias spot check: acme rows carry acme values, nike rows nike values.
    assert psql("SELECT min(views) FROM raw_instagram_acme.insights_totals_history") == "1000"
    assert psql("SELECT min(views) FROM raw_instagram_nike.insights_totals_history") == "2000"


def cp_organic_marts() -> None:
    """E organic gate (spec E VER-R1 / design D4/D5): first automated
    ``dbt test`` execution, on real dbt run + test + ls over acme.

    Invoked after ``cp_archive_acme`` (acme live @ corrida K+1 + full history)
    and before ``cp_nfr4`` (dbt adds only ``client_acme`` objects — the catalog
    assert stays safe; nike ``_history`` tables do not exist yet, so the
    TEN-S2 order is untouched). Runs the organic plan for acme and asserts the
    offline-unprovable spec-E scenarios: ORG-S1..S7 (window/unique outcomes),
    NOM-E-S1 (4 ``_history`` sources resolve), NOM-E-S2 (breakdowns source
    registered-but-unconsumed), HIG-S1 (no real client id).
    """
    from agency_analytics.pipeline_plan import build_plan, organic_marts

    plan = build_plan(["instagram", "tiktok_organic"])
    marts = organic_marts(["instagram", "tiktok_organic"])
    assert len(marts) == 3 and "organic_tiktok_profile_daily" in marts, (
        f"organic plan: unexpected resolver output {marts}"
    )
    # The wipe-sim seeds ONLY the archive-connector raw surfaces; build_plan
    # also lists the IG media / TT videos staging models (and the monitoring
    # chain), whose raw parents the sim never seeds — running them here would
    # fail on missing relations. The organic marts' ancestors are exactly
    # ORGANIC_MART_STAGING (the views over the seeded tables); selecting them
    # explicitly avoids dbt run-select ancestor ambiguity (design D4). The
    # assert keeps that constant consistent with the plan resolver's staging.
    assert set(ORGANIC_MART_STAGING) <= set(plan.models), (
        f"organic staging drift: {ORGANIC_MART_STAGING} vs {plan.models}"
    )
    select = " ".join(ORGANIC_MART_STAGING) + " " + " ".join(marts)
    marts_select = " ".join(marts)

    # VER-S1: real dbt run (staging views + organic marts) then explicit dbt
    # test on the three marts — green unique/not_null/accepted_values.
    run_dbt("acme", select, "organic", command="run")
    run_dbt("acme", marts_select, "organic_test", command="test")

    # ORG-S1/ORG-S3: window W (>90d, evicted from live at K+1) is served from
    # insights_totals_history — common metrics from history (acme bias 1000 ->
    # views 1000), window_label 'archived', gated metrics NULL (never
    # fabricated from history).
    w = psql_rows(
        "SELECT window_label || ':' || views::text || ':' || "
        "coalesce(follows_and_unfollows::text, 'NULL') || ':' || "
        "coalesce(profile_links_taps::text, 'NULL') "
        "FROM client_acme.organic_instagram_totals WHERE date_start = '2026-06-01'"
    )
    assert w == ["archived:1000:NULL:NULL"], f"ORG-S1/S3 window W row: {w}"
    # ORG-S1/S2: current window is 'recency'; exactly one row per window key
    # (no daily fan-out) and the current window answers the gated metric.
    windows = psql_rows(
        "SELECT date_start::text || '|' || date_end::text || '|' || window_label "
        "FROM client_acme.organic_instagram_totals ORDER BY date_start"
    )
    assert windows == [
        "2026-06-01|2026-06-30|archived",
        "2026-08-01|2026-08-31|recency",
    ], f"ORG-S2 window rows: {windows}"
    cur = psql_rows(
        "SELECT follows_and_unfollows::text FROM client_acme.organic_instagram_totals "
        "WHERE date_end = '2026-08-31'"
    )
    assert cur == ["1001"], f"ORG-S3 current-window gated answer: {cur}"

    # ORG-S4: acme 2026-08-05 — the K+1 live row carries the NULL no-data
    # follower marker (reach present) while history holds the corrida-K value
    # (bias*10+5 = 10005); the mart must read the archived value, never 0,
    # never NULL-shadowed.
    s4 = psql_rows(
        "SELECT coalesce(reach::text, 'NULL') || ':' || follower_count::text "
        "FROM client_acme.organic_instagram_daily WHERE report_date = '2026-08-05'"
    )
    assert s4 == ["1005:10005"], f"ORG-S4 08-05 follower merge: {s4}"
    # ORG-S5: history-only day 06-11 keeps its row with reach NULL and
    # follower from history (bias*10 + 11 = 10011).
    s5 = psql_rows(
        "SELECT coalesce(reach::text, 'NULL') || ':' || follower_count::text "
        "FROM client_acme.organic_instagram_daily WHERE report_date = '2026-06-11'"
    )
    assert s5 == ["NULL:10011"], f"ORG-S5 history-only day: {s5}"
    # ORG-S2/S6 grain: exact daily row set (older + mid + recent from both
    # sources; no fan-out, no invented days).
    daily = psql_rows(
        "SELECT report_date::text FROM client_acme.organic_instagram_daily ORDER BY report_date"
    )
    assert daily == [
        "2026-06-11",
        "2026-06-12",
        "2026-08-05",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
    ], f"ORG-S2/S6 daily rows: {daily}"
    # ORG-S6: TT profile series = the two run days (K + K+1), live/history
    # merged; unique(client_id, report_date) already green via dbt test.
    tt = psql_rows(
        "SELECT report_date::text || ':' || follower_count::text "
        "FROM client_acme.organic_tiktok_profile_daily ORDER BY report_date"
    )
    assert tt == ["2026-09-01:1001", "2026-09-02:1002"], f"ORG-S6 TT rows: {tt}"

    # NOM-E-S1: dbt ls --resource-type source resolves all four `_history`
    # tables at parse for the acme invocation (sources.yml jinja renders the
    # raw_instagram_acme / raw_tiktok_organic_acme tenant schemas).
    ls_out = run_dbt(
        "acme",
        "",
        "organic_ls",
        command="ls",
        extra=("--resource-type", "source"),
    ).stdout
    for source_id in (
        "raw_instagram.insights_totals_history",
        "raw_instagram.insights_totals__breakdowns_history",
        "raw_instagram.follower_count_history",
        "raw_tiktok_organic.profile_stats_history",
    ):
        assert source_id in ls_out, f"NOM-E-S1: {source_id} absent from dbt ls:\n{ls_out}"

    # NOM-E-S2: the breakdowns child source is registered-but-unconsumed in v1
    # — no model under models/ may select it (offline twin of the test_archiver
    # guard, asserted here against the mounted repo tree).
    models_dir = Path(__file__).resolve().parent.parent / "src" / "dbt_project" / "models"
    breakdowns_select = "source('raw_instagram', 'insights_totals__breakdowns_history')"
    offenders = [
        str(path)
        for path in sorted(models_dir.rglob("*.sql"))
        if breakdowns_select in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"NOM-E-S2: breakdowns source selected by: {offenders}"

    # HIG-S1: the real tracked-only client never appears in the E wipe-sim
    # surface (placeholders acme/nike/zeta only). Real ids are DERIVED from the
    # repo clients dir so the identifier is never embedded in this file.
    repo_clients = {
        p.stem for p in (Path(__file__).resolve().parent.parent / "clients").glob("*.yml")
    }
    real_ids = sorted(repo_clients - {"acme", "nike", "zeta", "_template"})
    e_files = [
        Path(__file__).resolve(),
        *sorted((Path(__file__).resolve().parent / "docker" / "wipe-sim" / "scripts").glob("*.py")),
    ]
    for real_id in real_ids:
        hits = [str(p) for p in e_files if real_id in p.read_text(encoding="utf-8")]
        assert hits == [], f"HIG-S1: real client id present in wipe-sim surface: {hits}"

    # ORG-S7 (negative): the grain guard is real — a seeded duplicate window
    # key makes the totals unique test FAIL; deleting the row re-greens it.
    psql(
        "INSERT INTO client_acme.organic_instagram_totals "
        "(client_id, date_start, date_end, window_label, views) VALUES "
        "('acme', '2026-06-01', '2026-06-30', 'recency', -1)"
    )
    dup = run_dbt("acme", "organic_instagram_totals", "organic_neg", command="test", check=False)
    assert dup.returncode != 0, "ORG-S7: duplicate window key did not fail the unique test"
    psql("DELETE FROM client_acme.organic_instagram_totals WHERE views = -1")
    run_dbt("acme", "organic_instagram_totals", "organic_regreen", command="test")


def cp_nightly_test_gate() -> None:
    """SDD-F WU5 (NGT-R2/R3, design D6/D7): real dbt test over the nightly
    acme select union — the first whole-nightly test-closure proof on the sim.

    Invoked after ``cp_organic_marts`` (NOT after ``cp_b_s1_b_s3``): the IG/TT
    raw surfaces and organic marts exist only after the D-era archive
    checkpoints, so a real test there would fail on missing relations (design
    D6 placement correction). ``dbt test`` adds no objects, so ``cp_nfr4``'s
    catalog assert stays undisturbed.

    The union mirrors the nightly ``${dbt_select}`` shape: ``dbt_plan_models()``
    (B-S1 select: ads/campaigns staging + monitoring chain + investment marts)
    plus the organic staging/marts over the archive-connector raw tables. The
    cross-assert pins ORGANIC_MART_STAGING to the plan resolver (E-era guard).

    Asserts exit-green (run_dbt check=True) + a PASS line in the dbt output
    (NGT-S5/S6), then proves STG-S1 for real: whole-project ``dbt ls`` no
    longer lists the two deleted orphan staging models.
    """
    from agency_analytics.pipeline_plan import build_plan, organic_marts

    organic_plan = build_plan(["instagram", "tiktok_organic"])
    assert set(ORGANIC_MART_STAGING) <= set(organic_plan.models), (
        f"organic staging drift: {ORGANIC_MART_STAGING} vs {organic_plan.models}"
    )
    marts = organic_marts(["instagram", "tiktok_organic"])
    assert len(marts) == 3 and "organic_tiktok_profile_daily" in marts, (
        f"organic plan: unexpected resolver output {marts}"
    )
    union = " ".join([dbt_plan_models(), *ORGANIC_MART_STAGING, *marts])
    out = run_dbt("acme", union, "nightly_gate", command="test")
    assert "PASS" in out.stdout, f"nightly gate: dbt test not green:\n{out.stdout}"

    # STG-S1 real: the whole-project ls drops the two deleted orphans.
    ls_out = run_dbt("acme", "", "nightly_ls", command="ls").stdout
    for orphan in ("stg_facebook__page_profile", "stg_instagram__business_profile"):
        assert orphan not in ls_out, f"STG-S1: orphan still listed by dbt ls:\n{ls_out}"


def cp_nfr4() -> None:
    """NFR-4: exact catalog for 2 clients x 3 overlapping connectors + the 4
    archive connectors' raw schemas (WU4 ``_history`` hosts, TEN-R1); no
    legacy shared ``raw_*`` schema appears."""
    expected = {
        "public",
        "staging",
        "client_acme",
        "client_nike",
        "raw_meta_acme",
        "raw_tiktok_acme",
        "raw_google_acme",
        "raw_meta_nike",
        "raw_tiktok_nike",
        "raw_google_nike",
        "raw_instagram_acme",
        "raw_instagram_nike",
        "raw_tiktok_organic_acme",
        "raw_tiktok_organic_nike",
    }
    actual = schemas()
    assert actual == expected, (
        f"NFR-4: schema set mismatch\nexpected={sorted(expected)}\nactual={sorted(actual)}"
    )
    legacy = sorted(actual & {"raw_meta", "raw_tiktok", "raw_google"})
    assert legacy == [], f"NFR-4: legacy shared raw_* present: {legacy}"


def cp_freeze_db_gate() -> None:
    """freeze_regression DB path is client-aware (raw_instagram_<client>, D8)."""
    seed("acme", "instagram")
    code = (
        "import json, os;"
        "from agency_analytics.freeze_regression import CONNECTOR_CHECKS, check_table_via_db;"
        "dsn = ('postgresql://' + os.environ['POSTGRES_USER'] + ':'"
        " + os.environ['POSTGRES_PASSWORD'] + '@' + os.environ['POSTGRES_HOST']"
        " + ':5432/' + os.environ['POSTGRES_DB']);"
        "r = check_table_via_db(dsn, CONNECTOR_CHECKS['instagram'], client_id='acme');"
        "print(json.dumps(r.as_dict() if hasattr(r, 'as_dict') else str(r)))"
    )
    out = exec_pipeline("/app", ["python", "-c", code]).stdout.strip()
    report = json.loads(out)
    assert report["ok"] is True, f"freeze DB gate (healthy): {report}"
    assert report["table"] == "raw_instagram_acme.insights_daily", (
        f"freeze DB gate: wrong schema scope {report}"
    )
    psql("UPDATE raw_instagram_acme.insights_daily SET reach = 500")
    out2 = exec_pipeline("/app", ["python", "-c", code]).stdout.strip()
    report2 = json.loads(out2)
    assert report2["ok"] is False, f"freeze DB gate (frozen): {report2}"
    assert any(f["column"] == "reach" for f in report2["frozen"]), f"freeze DB gate: {report2}"


def cp_concat_fail_loud() -> None:
    """Final 5.3 assert: pipeline.sh dbt_vars concat fails loud on hostile ids."""
    code = "\n".join(
        [
            "import json",
            "# pipeline.sh dbt_vars concat replica: '{\"client_id\": \"' + c + '\"}'",
            "def concat(c): return '{\"client_id\": \"' + c + '\"}'",
            "assert json.loads(concat('acme'))['client_id'] == 'acme'",
            "for hostile in ('bob\"OR\"1=1', 'a\\\\', 'x\"}; DROP'):",
            "    try:",
            "        json.loads(concat(hostile))",
            "        raise SystemExit('NOT fail-loud: ' + hostile)",
            "    except json.JSONDecodeError:",
            "        pass",
            "print('concat fail-loud OK')",
        ]
    )
    out = exec_pipeline("/app", ["python", "-c", code]).stdout.strip()
    assert out == "concat fail-loud OK", f"5.3 concat assert failed: {out}"


def test_wipe_sim_end_to_end(sim_stack) -> None:
    """Boot -> init OFF -> seeds -> dbt per client -> RBAC ON -> teardown."""
    before = snapshot_real_stack()

    compose("build", "--no-cache", timeout=2400)
    compose("up", "-d", "--wait", timeout=900)

    ready = False
    for _ in range(60):
        try:
            if psql("SELECT 1") == "1":
                ready = True
                break
        except AssertionError:
            pass
        run(["sleep", "2"])
    assert ready, "postgres did not become ready"

    logs = compose("logs", "--no-color", "postgres").stdout
    assert "01-create-pipeline-tables.sql" in logs, "init 01 did not run"
    assert "02-bootstrap-rbac.sh v2" in logs, "init 02 v2 did not run"

    # D-S1 OFF (default boot): no metabase_reader role, init re-run OFF is safe.
    assert psql_rows("SELECT rolname FROM pg_roles WHERE rolname = 'metabase_reader'") == [], (
        "D-S1 OFF: role exists at default boot"
    )
    compose("exec", "-T", "postgres", "/docker-entrypoint-initdb.d/02-bootstrap-rbac.sh")

    models = dbt_plan_models()
    assert "stg_meta__ads" in models and "pipeline_monitoring" in models, f"plan select: {models}"

    cp_a_s1_s2_s3()
    cp_monitoring_seed()
    cp_b_s1_b_s3("acme", models, "acme")
    cp_b_s1_b_s3("nike", models, "nike")
    cp_c_s1()
    cp_g_s1()
    # SDD-D WU4 archive checkpoints (docker-marked; outside the baseline count):
    # live seeds pre-enable -> acme CATCH-S1/ARC-S2/S2b/S3 -> catalog -> RBAC ON
    # -> nike TEN-S2/TEN-S1 -> teardown drops the nike archive schemas too.
    cp_archive_live_seed_k()
    acme_archive = cp_archive_acme()
    # E WU-E4: organic gate runs real dbt run + test + ls between acme's full
    # archive state (live @ K+1 + complete _history) and the catalog assert —
    # dbt adds only client_acme objects, so cp_nfr4's schema set is unchanged.
    cp_organic_marts()
    cp_nightly_test_gate()
    cp_nfr4()
    cp_d_gate_off_on()
    cp_archive_nike_ten(acme_archive)
    cp_b_s2_teardown()
    cp_freeze_db_gate()
    cp_concat_fail_loud()

    assert_real_stack_unchanged(before, "post-scenarios")
