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


def fingerprint_raw(client: str, conn: str) -> str:
    return psql(
        "SELECT count(*) || ':' || md5(string_agg(ad_id || '|' || spend::text, ','"
        f" ORDER BY ad_id)) FROM raw_{conn}_{client}.ads"
    )


def run_dbt(client_id: str, select_models: str, tag: str) -> None:
    """dbt run with real pipeline select (pipeline.sh contract), /tmp artifacts."""
    exec_pipeline(
        "/app/src/dbt_project",
        [
            "dbt",
            "run",
            "--select",
            select_models,
            "--vars",
            json.dumps({"client_id": client_id}),
            "--profiles-dir",
            ".",
            "--target-path",
            f"/tmp/dbt_target_{tag}",
            "--log-path",
            f"/tmp/dbt_log_{tag}",
        ],
    )


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
    """B-S2/B-R5: DROP SCHEMA client_nike CASCADE + raw drop leaves acme intact."""
    psql("DROP SCHEMA IF EXISTS client_nike CASCADE")
    for conn in ("meta", "tiktok", "google"):
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


def cp_nfr4() -> None:
    """NFR-4: exact catalog for 2 clients x 3 overlapping connectors; no legacy."""
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
    cp_nfr4()
    cp_d_gate_off_on()
    cp_b_s2_teardown()
    cp_freeze_db_gate()
    cp_concat_fail_loud()

    assert_real_stack_unchanged(before, "post-scenarios")
