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
                          │ dlt (extract + load)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    POSTGRESQL - DATA WAREHOUSE                       │
│                                                                     │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐           │
│  │   raw_meta    │  │  raw_tiktok   │  │  raw_google   │  ← Domains │
│  │  raw_facebook │  │ raw_instagram │  │raw_tiktok_org │           │
│  │  raw_youtube  │  │ raw_pinterest │  │   raw_ga4     │           │
│  │   raw_gtm     │                ...                               │
│  └───────────────┘  └───────────────┘  └───────────────┘           │
│                                                                     │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐           │
│  │  client_acme  │  │  client_nike  │  │  client_xyz   │  ← Clients │
│  └───────────────┘  └───────────────┘  └───────────────┘           │
└────────────┬──────────────────────────────────┬───────────────────┘
             │ dbt (transformation)              │
             ▼                                  ▼
┌──────────────────────────┐        ┌─────────────────────────────────┐
│     agency_analytics_net │        │     agency_internal_net         │
│                          │        │                                 │
│  ┌────────────────────┐  │        │  ┌──────────────────────────┐  │
│  │      Metabase      │  │        │  │    Pipeline Worker        │  │
│  │  (clients + team)  │  │        │  │    (dlt + dbt Core)       │  │
│  └────────────────────┘  │        │  └──────────────────────────┘  │
└──────────────────────────┘        └─────────────────────────────────┘
```

---

## Docker Network Architecture

Two external Docker networks. Once created, they persist independently of container lifecycles.

```bash
make setup-networks
# or: ./scripts/setup-networks.sh
```

### Network 1: `agency_analytics_net`
- **Members:** Metabase, Postgres
- **Purpose:** Serve dashboards to clients and agency team
- **Constraint:** Metabase cannot see or reach the pipeline worker

### Network 2: `agency_internal_net`
- **Members:** Pipeline Worker (dlt + dbt), Postgres
- **Purpose:** Data ingestion and transformation
- **Constraint:** No ports exposed to the host. Operates exclusively via `docker exec`

### Security Principle

If Metabase is compromised, the attacker can only see `agency_postgres` on its network. There is no network path to the pipeline container, its ingestion scripts, or API credentials.

---

## Multi-Tenant Architecture

### Schema-Based Isolation

Each entity has its own schema in Postgres. No schema is shared between clients or between raw domains and client data.

```
Postgres (agency_dw)
│
├── raw_meta              ← Raw Meta Ads data
├── raw_tiktok            ← Raw TikTok Ads data
├── raw_google            ← Raw Google Ads data
├── raw_facebook          ← Raw Facebook Pages data
├── raw_instagram         ← Raw Instagram Graph data
├── raw_tiktok_organic    ← Raw TikTok Organic data
├── raw_youtube           ← Raw YouTube Data data
├── raw_pinterest         ← Raw Pinterest data
├── raw_ga4               ← Raw Google Analytics 4 data
├── raw_gtm               ← Raw Google Tag Manager data
│
├── staging               ← dbt intermediate models (cleaning + typing)
│
├── client_acme           ← Final marts for Acme client
├── client_nike           ← Final marts for Nike client
└── client_xyz            ← Final marts for XYZ client
```

### YAML Client Configuration

Each client has a configuration file in `clients/`. This file declares which domains it consumes and the API access credentials.

```yaml
# clients/acme.yml
client_id: acme
client_name: Acme Corp
schema: client_acme

connectors:
  meta:
    enabled: true
    account_id: "1234567890"
    token_env: META_ACCESS_TOKEN_ACME
  google:
    enabled: true
    customer_id: "987-654-3210"
    token_env: GOOGLE_ADS_TOKEN_ACME
  tiktok:
    enabled: false
  facebook:
    enabled: true
    page_id: "987654321"
    token_env: FACEBOOK_ACCESS_TOKEN_ACME
  instagram:
    enabled: true
    instagram_business_id: "987654321"
    token_env: INSTAGRAM_ACCESS_TOKEN_ACME
  tiktok_organic:
    enabled: false
  youtube:
    enabled: true
    channel_id: "UCxxxxxx"
    token_env: YOUTUBE_API_KEY_ACME
  pinterest:
    enabled: false
  ga4:
    enabled: false
  gtm:
    enabled: false

dbt:
  tags:
    - acme
```

dlt and dbt read this file to determine what to extract and which models to run for each client.

### Data Flow Per Client

```
clients/acme.yml
      │
      ├──► dlt run_meta.py           ──► raw_meta.ads
      ├──► dlt run_google.py         ──► raw_google.ads
      ├──► dlt run_facebook.py       ──► raw_facebook.page_posts
      ├──► dlt run_instagram.py      ──► raw_instagram.media
      ├──► dlt run_youtube.py        ──► raw_youtube.channel_stats
      ├──► dlt run_pinterest.py      ──► raw_pinterest.pins
      ├──► dlt run_ga4.py            ──► raw_ga4.daily_stats
      ├──► dlt run_gtm.py            ──► raw_gtm.containers
      │
      └──► dbt run --vars '{client_id: "acme"}' ──► client_acme.campaign_performance
                                                   └──► client_acme.ad_spend_summary
```

---

## dbt Architecture

### Layer Structure

```
dbt_project/
├── models/
│   ├── staging/              ← Cleaning, typing, column renaming
│   │   ├── stg_meta__ads.sql
│   │   ├── stg_tiktok__ads.sql
│   │   ├── stg_google__ads.sql
│   │   ├── stg_facebook__page_posts.sql
│   │   ├── stg_instagram__media.sql
│   │   ├── stg_tiktok_organic__videos.sql
│   │   ├── stg_youtube__videos.sql
│   │   ├── stg_pinterest__pins.sql
│   │   ├── stg_ga4__daily_stats.sql
│   │   └── stg_gtm__tags.sql
│   │                        ← (24 staging models total)
│   ├── intermediate/         ← Cross-domain joins and enrichment
│   │   └── int_unified_spend.sql
│   │
│   └── marts/                ← Final client-facing models (with macros)
│       ├── campaign_performance.sql
│       └── ad_spend_summary.sql
│
├── macros/
│   ├── generate_schema_name.sql   ← Override for multi-tenant
│   └── get_client_sources.sql     ← Reads clients/*.yml at runtime
│
├── sources.yml               ← 10 raw source declarations
├── schema.yml                ← 75 test definitions across 27 models
└── dbt_project.yml
```

### Multi-Tenant Macro

dbt generates the output schema based on the active client. The `generate_schema_name` macro is overridden to respect the `client_<id>` convention:

```sql
-- macros/generate_schema_name.sql
{% macro generate_schema_name(custom_schema_name, node) -%}
  {%- if custom_schema_name is not none -%}
    {{ custom_schema_name | trim }}
  {%- else -%}
    {{ target.schema }}
  {%- endif -%}
{%- endmacro %}
```

Marts models run with client variables:

```bash
# scripts/pipeline.sh uses --vars instead of --select tag:
dbt run --vars '{"client_id": "acme"}' --profiles-dir .
```

---

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

Access roles are created by the **codified, idempotent init bootstrap** — there is no
manual SQL to run (spec B6). The Postgres entrypoint executes the scripts in
`services/db/init/` on the first start of an empty volume, in lexicographic order:

1. `01-create-pipeline-tables.sql` — monitoring tables `public.pipeline_runs` and
   `public.pipeline_run_steps` (+ indexes).
2. `02-bootstrap-rbac.sh` — read-only role, schemas and grants; re-runnable (every
   statement is idempotent, NFR2).

| Role | Permissions | Used By | Created By |
|---|---|---|---|
| `agency_admin` (env `POSTGRES_USER`) | Superuser / object owner | dlt, dbt, administration | postgres image (env) |
| `metabase_reader` | LOGIN; SELECT on `public`, `staging`, `raw_*` | Metabase | init `02-bootstrap-rbac.sh` |

`02-bootstrap-rbac.sh` idempotently: (1) creates the `metabase_reader` role with a
guard (on re-run it only syncs LOGIN/PASSWORD from `METABASE_READER_PASSWORD`, which
must match `MB_DB_PASS` of Metabase); (2) creates the `staging` schema plus the 10
`raw_*` schemas; (3) applies `GRANT USAGE` on the schemas and `GRANT SELECT` on the
`public` monitoring tables; (4) applies `ALTER DEFAULT PRIVILEGES FOR ROLE
${POSTGRES_USER}` in `public`, `staging`, and each `raw_*` — since the future object
creator (dlt/dbt) is `POSTGRES_USER`, new objects stay readable by `metabase_reader`
**without manual GRANT** (spec B2).

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
requires prior dlt loads that populate the `raw_*` schemas: staging views over empty
raw tables fail. Run `make pipeline` (or the nightly pipeline) to populate `raw_*`
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
