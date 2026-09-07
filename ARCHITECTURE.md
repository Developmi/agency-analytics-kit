# Architecture - Agency Analytics Kit

---

## Overview

The system follows an **ELT** pattern (Extract → Load → Transform): raw data lands in Postgres first, then dbt transforms it. This lets you reprocess transformations without re-extracting from external APIs.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        EXTERNAL SOURCES                              │
│         Meta Ads · TikTok Ads · Google Ads                           │
│      Facebook · Instagram · TikTok Organic · YouTube · Pinterest     │
│                          GA4 · GTM                                   │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ dlt (extract + load, per client)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    POSTGRESQL - DATA WAREHOUSE  (agency_dw)          │
│                                                                     │
│  Raw silos — one raw_<connector>_<client_id> per client × enabled   │
│  connector (created on first load by dlt):                          │
│    raw_meta_acme · raw_google_acme · raw_meta_nike · raw_tiktok_nike│
│  Legacy shared raw_* (raw_meta, raw_google, …) stays intact but is  │
│  no longer written (fresh-start topology, spec G-R1)                │
│                                                                     │
│  dbt outputs per client — client_acme · client_nike · client_<id>   │
│  Shared observability — staging + public (pipeline_runs chain)      │
└────────────┬───────────────────────────────────────────────────────┘
             │ dbt: one run per client (--vars client_id, pipeline.sh)
             ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Networks (external; see "Docker Network Architecture"):            │
│    agency_internal_net   ← Postgres · Pipeline Worker · Metabase    │
│    agency_analytics_net  ← Metabase (front/BI access net)           │
│  Metabase is a member of BOTH networks.                             │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Docker Network Architecture

Two external Docker networks. Once created, they persist independently of container lifecycles.

```bash
make setup-networks
# or: ./scripts/setup-networks.sh
```

### Network 1: `agency_analytics_net`
- **Members:** Metabase (front net for BI access)
- **Purpose:** Serve dashboards to clients and agency team

### Network 2: `agency_internal_net`
- **Members:** Postgres 16, Pipeline Worker (dlt + dbt), and Metabase
- **Purpose:** Data ingestion and transformation, database access
- **Constraint:** Pipeline Worker exposes no ports to the host and operates
  exclusively via `docker exec`; Postgres publishes only `127.0.0.1:5432` on
  the host

### Honest Network Posture

Metabase is a member of **both** networks: its compose file
(`services/metabase/compose.yaml`) attaches it to `agency_internal_net` so it
can reach `agency_postgres`, and to `agency_analytics_net` as the front net. It
therefore shares a bridge with the Pipeline Worker — the v1 claim that
"Metabase cannot see or reach the pipeline worker" is **not** guaranteed by the
current compose topology. Real defense-in-depth for a Metabase compromise is:

1. **Least-privilege DB credentials**: Metabase connects with the dedicated
   read-only role `metabase_reader` (opt-in, OFF by default — see *Postgres
   Users and Access*), never with the operational `agency_admin`.
2. **Pipeline Worker keeps its secrets**: API tokens live in its own
   environment; the container runs no server and exposes no ports to other
   containers or the host.
3. **Postgres is host-private**: bound to `127.0.0.1` on the host, reachable
   from containers only on `agency_internal_net`.

## Multi-Tenant Architecture

### Schema-Based Isolation

Multi-tenancy follows a bridge/silo-by-layer topology (`multi-tenancy-real`):

- **Ingest = silo per client.** Each (active client × enabled connector) loads
  into its own raw namespace `raw_<connector>_<client_id>`; dlt full `replace`
  per tenant is the isolation boundary — no row ever lands in another tenant's
  schema and no `client_id` query filter is relied on. Staging models read only
  their own tenant's raw namespace (sources.yml jinja routing, M1).
- **Transform = pooled code, per-client output.** dbt runs once per client
  (`--vars client_id`) and tenant models land in `client_<id>` via the
  `generate_schema_name` macro; no folder-level `+schema` forces them anywhere
  else (a missing `client_id` resolves to `client_default`, never shared).
- **Observability = shared.** The monitoring chain stays shared, append-only
  and client-partitioned: `public.pipeline_runs` / `public.pipeline_run_steps`,
  `staging.stg_public__*` → `int_pipeline_daily_summary` →
  `public.pipeline_monitoring`.
- **Legacy `raw_*` intact and unused (G-R1).** The pre-change shared raw
  schemas still exist on existing volumes but are no longer written by this
  version; an optional future drop is documented, not executed here (G-R3).

```
Postgres (agency_dw)
│
├── raw_meta_acme        ← one raw_<connector>_<client_id> per client ×
│   raw_google_acme        enabled connector (dlt, created on first
│   raw_meta_nike          load; e.g. also raw_tiktok_nike)
│
├── client_acme          ← Acme dbt output (staging/intermediate/marts)
├── client_nike          ← Nike dbt output
├── client_<id>          ← one per tracked client (see clients/*.yml)
│
├── staging              ← shared observability staging (stg_public__*)
├── public               ← shared monitoring tables (pipeline_runs, …)
│
└── raw_meta, raw_google,  ← legacy shared raw: intact but unused
    … (v1 raw_*)             (fresh-start G-R1; cleanup = G-R3, not run)
```

### YAML Client Configuration

Each client has a configuration file in `clients/` (`_template.yml` is the
canonical contract). The file declares whether the client is `active`
(inactive clients are skipped by `scripts/pipeline.sh`), which domains it
consumes, and the API access credentials. Only connectors with
`enabled: true` run for that client; `schema` stays part of the contract
(spec E-R1).

```yaml
# clients/acme.yml
client_id: acme
client_name: "Acme Corp"
schema: client_acme
active: false   # pipeline.sh skips inactive clients; flip to true to run it

connectors:
  meta:
    enabled: true
    account_id: "1234567890"
    token_env: META_ACCESS_TOKEN_ACME
  tiktok:
    enabled: false
    account_id: ""
    token_env: TIKTOK_ACCESS_TOKEN_ACME
  google:
    enabled: true
    customer_id: "987-654-3210"
    token_env: GOOGLE_ADS_TOKEN_ACME
  facebook:
    enabled: false
    page_id: ""
    token_env: FACEBOOK_ACCESS_TOKEN_ACME
  instagram:
    enabled: false
    instagram_business_id: ""
    token_env: INSTAGRAM_ACCESS_TOKEN_ACME
  tiktok_organic:
    enabled: false
    open_id: ""
    token_env: TIKTOK_ORGANIC_ACCESS_TOKEN_ACME
  youtube:
    enabled: false
    channel_id: ""
    token_env: YOUTUBE_API_KEY_ACME
  pinterest:
    enabled: false
    board_id: ""
    token_env: PINTEREST_ACCESS_TOKEN_ACME
  ga4:
    enabled: false
    property_id: ""
    token_env: GA4_ACCESS_TOKEN_ACME
  gtm:
    enabled: false
    account_path: ""
    token_env: GTM_ACCESS_TOKEN_ACME

dbt:
  tags:
    - acme
```

dlt and dbt read this file to determine what to extract and which models to run
for each client (`run_<connector>.py` consumes it via the shared
`client_config` helper; `client_id` derives the raw dataset
`raw_<connector>_<client_id>` and the dbt output schema `client_<id>`).

### Data Flow Per Client

```
clients/acme.yml (active: true)
      │
      ├──► for each connector with enabled: true
      │       docker exec agency_pipeline python src/connectors/run_<conn>.py --client acme
      │         └──► dlt loads into raw_<connector>_acme (full replace)
      │               e.g. raw_meta_acme.ads · raw_google_acme.ads
      │
      └──► dbt run --select '<plan models>' --vars '{"client_id": "acme"}'
              └──► client_acme.campaign_performance
              └──► client_acme.ad_spend_summary
```

---

## dbt Architecture

### Layer Structure

```
dbt_project/
├── models/                 ← 35 models total (staging/intermediate/marts)
│   ├── staging/            ← cleaning, typing, column renaming
│   │   ├── stg_meta__ads.sql, stg_google__ads.sql, stg_facebook__*.sql, …
│   │   ├── stg_public__pipeline_runs.sql       ← observability (shared)
│   │   └── stg_public__pipeline_run_steps.sql  ← observability (shared)
│   ├── intermediate/       ← cross-domain joins and enrichment
│   │   ├── int_unified_spend.sql             ← tenant (client_<id>)
│   │   └── int_pipeline_daily_summary.sql    ← observability (staging)
│   └── marts/              ← final client-facing models
│       ├── ad_spend_summary.sql           ← tenant (client_<id>)
│       ├── campaign_performance.sql       ← tenant (client_<id>)
│       └── pipeline_monitoring.sql        ← observability (public)
│
├── macros/
│   └── generate_schema_name.sql   ← routes tenant models to client_<id>;
│                                    explicit-schema models pass through
│                                    (get_client_sources.sql deleted,
│                                    superseded by M1 source routing)
│
├── sources.yml               ← 10 connector sources with jinja schema
│                                raw_<connector>_<client_id> (M1) + public
├── schema.yml                ← 74 data tests across 33 models
└── dbt_project.yml           ← vars: client_id only ("default")
```

### Per-Client Schema Routing

`generate_schema_name` routes every model to `client_<id>` unless the model
carries an explicit `schema` config. Tenant models (connector staging,
intermediate, marts) have no schema config and land in
`client_{{ var('client_id') }}`; the observability models set an explicit schema
in their `config()` and pass through unchanged (`stg_public__*` and
`int_pipeline_daily_summary` → `staging`; `pipeline_monitoring` → `public`). A
run without `--vars` resolves the dbt_project.yml default `client_id: "default"`
→ `client_default` (fail-fast — never a shared schema). Raw source schemas are
jinja-rendered from the same `var('client_id')` in sources.yml (M1), so every
`stg_*` reads its own tenant's `raw_<connector>_<client_id>`.

```sql
-- macros/generate_schema_name.sql
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is not none -%}
    {{ custom_schema_name | trim }}
  {%- else -%}
    client_{{ var('client_id') }}
  {%- endif -%}
{%- endmacro %}
```

The pipeline runs dbt once per active client with only `client_id` in `--vars`
(the model `--select` comes from the plan in `scripts/pipeline.sh`):

```bash
dbt run --select '<plan models>' --vars '{"client_id": "acme"}' --profiles-dir .
```

## Orchestration: Nightly Pipeline

The pipeline runs from the host via cron. Before any step, it validates container health.

### Execution Flow

```
cron (02:00 AM)
      │
      ▼
scripts/pipeline.sh
      │
      ├── [1] Validate containers (postgres, pipeline_worker)
      │         │
      │         ├── OK  ──────────────────────────────────┐
      │         └── FAIL ──► Telegram alert ──► ABORT     │
      │                                                   │
      ▼                                                   │
      ├── [2] For each active client in clients/*.yml     │◄──┘
      │         │
      │         ├── dlt: extract enabled domains
      │         │         └── FAIL ──► Telegram alert ──► next client
      │         │
      │         └── dbt: run --vars '{client_id: ...}' via docker exec
      │                   └── FAIL ──► Telegram alert ──► next client
      │
      └── [3] Telegram: execution summary (successes + failures)
```

### Container Health Check

```bash
check_container() {
  local name=$1
  local status=$(docker inspect --format='{{.State.Health.Status}}' "$name" 2>/dev/null)
  if [ "$status" != "healthy" ]; then
    send_telegram "⚠️ Container $name is not healthy (status: $status). Pipeline aborted."
    exit 1
  fi
}
```

---

## Postgres Users and Access

Access is created by the **codified, idempotent init bootstrap** — there is no
manual SQL to run. The Postgres entrypoint executes the scripts in
`services/db/init/` on the first start of an empty volume, in lexicographic
order:

1. `01-create-pipeline-tables.sql` — monitoring tables `public.pipeline_runs`
   and `public.pipeline_run_steps` (+ indexes).
2. `02-bootstrap-rbac.sh` — shared `staging` schema + **opt-in** Metabase
   reader grants; re-runnable (idempotent, exit 0).

**Operational honesty (A-S4):** `agency_admin` (env `POSTGRES_USER`) is the ONLY
operational writer. Both dlt and dbt connect as it, so raw tenant schemas
(`raw_<connector>_<client_id>`) and dbt outputs (`client_<id>`) are created by
the same role that owns them. Per-tenant database users are intentionally NOT
introduced (out of scope): the physical namespace per client is the isolation
boundary and no `client_id` query filter is relied on for access control.

| Role | Permissions | Used By | Created By |
|---|---|---|---|
| `agency_admin` (env `POSTGRES_USER`) | Superuser / object owner — sole operational writer | dlt, dbt, administration | postgres image (env) |
| `metabase_reader` | **OPT-IN** (default OFF): LOGIN + USAGE/SELECT over non-system schemas | Metabase | init `02-bootstrap-rbac.sh`, only when `METABASE_READER_ENABLED=true` |

`02-bootstrap-rbac.sh` (v2): (1) creates the shared `staging` schema
(`IF NOT EXISTS`); (2) honors the strict boolean gate `METABASE_READER_ENABLED`
— OFF by default, and any value other than the exact string `true` means OFF:
no `metabase_reader` role and no grants exist (scenario D-S1); (3) when ON,
creates/syncs the role from `METABASE_READER_PASSWORD` (must match Metabase's
`MB_DB_PASS`), runs a catch-up `GRANT USAGE` / `GRANT SELECT ON ALL TABLES`
over every non-system schema enumerated from `information_schema.schemata`
(covers tenant schemas created before the enable), and applies **global**
`ALTER DEFAULT PRIVILEGES FOR ROLE agency_admin` (PG16, no `IN SCHEMA`) so
objects created later by dlt/dbt stay readable without manual GRANT. Raw and
client schemas are NOT pre-created by init — dlt and dbt create them on first
load/build (create-on-first-load); init only guarantees future readability via
default privileges (spec D-R2).

### Metabase posture (opt-in, spec F-R2)

Metabase is **not** part of the default stack: the compose service is gated
behind the `metabase` profile (`make docker-up-all` / `make docker-metabase`
enable it; `make docker-up` starts only Postgres + Pipeline). The DB reader
role follows the same gate via `METABASE_READER_ENABLED=true`. There is **no
per-tenant BI isolation in OSS** (tenant-scoped Metabase permissions are a
Pro/EE feature): a deployer-side Metabase instance serves curated schemas to
whichever of its own user accounts the deployer allows. The application
database connection (`MB_DB_*`) is restricted to the `metabase_reader`
credentials, so Metabase never carries the operational `agency_admin`
credentials.

### Post-wipe recovery cycle (resilience, spec B)

The whole Docker state is disposable: `down -v` removes the `pgdata` volume and the
next start regenerates everything — zero manual SQL (NFR1):

```bash
# 1) Destroy the whole Docker state
docker compose -f services/db/compose.yaml down -v        # removes the pgdata volume
docker compose -f services/pipeline/compose.yaml down     # stops the dbt/dlt worker

# 2) Rebuild from source (local image, root Dockerfile — never trust cached images, D1)
docker compose -f services/db/compose.yaml up -d          # init 01+02 run on the empty volume
docker compose -f services/pipeline/compose.yaml up -d --build   # image rebuilt

# 3) Verify and bootstrap the monitoring chain
docker ps            # both containers "healthy" (real compose healthchecks, spec B3)
make db-bootstrap    # dbt build of the monitoring chain on the clean DB
```

Note: `make db-bootstrap` rebuilds the monitoring chain
(`stg_public__*` → `int_pipeline_daily_summary` → `pipeline_monitoring`) on a clean DB
with no external data. The **full graph** (per-connector models and investment marts)
requires prior dlt loads that populate the tenant raw schemas
(`raw_<connector>_<client_id>`): staging models over empty raw tables fail. Run
`make pipeline` (or the nightly pipeline) to populate the tenant raw schemas
before a full build.

---

## Design Decisions

| Decision | Discarded Alternative | Reason |
|---|---|---|
| Schema per client | DB per client | Lower operational complexity, dbt handles it well |
| dbt Core | dbt Cloud | No free tier limits, full control on VPS |
| Cron + shell | Prefect / Airflow | No additional services, not overengineering for MVP |
| Telegram alerts | Email / PagerDuty | Simple API, no OAuth, instant delivery |
| YAML per client | Config table in Postgres | Git-versionable, simpler for MVP |
| NocoDB removed | - | Metabase covers visual data inspection needs |
