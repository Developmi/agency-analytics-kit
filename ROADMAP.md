# Roadmap - Agency Analytics Kit

---

## v1.0.0 - MVP ✅ Completed

> The MVP is complete and production-ready.

### Infrastructure

- [x] Docker networks created (`setup-networks.sh`)
- [x] Postgres 16 with healthcheck
- [x] Pipeline worker with dlt + dbt
- [x] Metabase with read-only user (`metabase_reader`)
- [x] Network isolation between services
- [x] Postgres bound to `127.0.0.1:5432` only

### Pipeline & Orchestration

- [x] `scripts/pipeline.sh` with container health validation
- [x] Telegram alerts (failure + summary)
- [x] Per-client loop over YAML config
- [x] Cron-ready (add `0 2 * * * /path/scripts/pipeline.sh` to crontab)
- [x] Logs to `/var/log/agency_pipeline.log` with local fallback
- [x] `load_dotenv()` - credentials from `.env`

### Connectors (dlt)

- [x] Meta Ads - ads, campaigns
- [x] TikTok Ads - ads, campaigns
- [x] Google Ads - ads, campaigns
- [x] Facebook Pages - posts, page insights
- [x] Instagram Business - media, insights
- [x] TikTok Organic - profile stats, videos
- [x] YouTube Data - channel stats, videos, daily analytics
- [x] Pinterest - boards, pins, board insights
- [x] GA4 - daily stats, page analytics, event analytics
- [x] GTM - containers, tags, triggers
- [x] Exponential backoff (max 5 retries) on all connectors
- [x] Rate-limit and token-expiry handling on all connectors

### dbt Models

- [x] 24 staging models (snake_case, explicit casts)
- [x] `int_unified_spend` (Meta + TikTok + Google Ads cross-platform)
- [x] `campaign_performance` mart (CTR, CPC, CPM per campaign)
- [x] `ad_spend_summary` mart (daily spend per platform)
- [x] `generate_schema_name` multi-tenant macro
- [x] `get_client_sources` macro (runtime YAML reading)
- [x] `sources.yml` - 10 raw sources declared
- [x] `schema.yml` - 75 test definitions across 27 models
- [x] Client tags on intermediate and marts models

### Multi-Tenant

- [x] `clients/_template.yml` with all 10 connectors (organic ones disabled by default)
- [x] `clients/acme.yml` - Meta + Google active
- [x] `clients/nike.yml` - Meta + TikTok active
- [x] Per-client schema routing via `generate_schema_name`
- [x] All tokens read from YAML `token_env` (no hardcoded env vars)

### Testing & Quality

- [x] 92 tests - mock-based, no live API calls
- [x] ruff linting (zero E501 violations)
- [x] mypy type checking (no issues in source files)
- [x] pytest config in `pyproject.toml`

### Security

- [x] `.gitignore` - blocks `.env`, `pgdata/`, `*.pyc`, etc.
- [x] Postgres on `127.0.0.1:5432` only
- [x] Metabase with read-only Postgres user
- [x] Docker network isolation
- [x] `SECURITY.md` with reporting guidelines
- [x] `profiles.yml` - dbt connection via env vars

### Documentation

- [x] `README.md` - OSS-ready with badges, quick start, structure
- [x] `ARCHITECTURE.md` - full design doc with network diagrams
- [x] `DEVELOPMENT.md` - guide for connectors, clients, dbt models
- [x] `CHANGELOG.md` - keep a changelog format
- [x] `CONTRIBUTING.md` - contribution guidelines
- [x] `CODE_OF_CONDUCT.md` - contributor covenant
- [x] `SECURITY.md` - vulnerability reporting
- [x] `LICENSE` - MIT
- [x] `.env.example` with all variables documented
- [x] GitHub issue templates (bug + feature)
- [x] GitHub PR template
- [x] Makefile with uv + quality + docker targets

---

## Phase 2 - Post-MVP

Improvements planned for after the initial release is stable in production.

### Observability

- [x] Table `pipeline_runs` + `pipeline_run_steps` in Postgres (auto-created via init SQL)
- [x] dbt staging + intermediate + mart models for pipeline monitoring data
- [ ] Internal Metabase dashboard for pipeline monitoring
- [ ] Telegram watchdog alert if pipeline hasn't run in 26 hours
- [ ] Data volume metrics per connector and client

### More Connectors

- [ ] LinkedIn Ads
- [ ] Pinterest Ads (separate from organic)
- [ ] Shopify (e-commerce clients)
- [ ] Automatic OAuth token refresh (Meta, Google)

### Client Management

- [ ] CLI onboarding script (`./scripts/add_client.sh`)
- [ ] CLI offboarding script (pause without data loss)
- [ ] Config table in Postgres as YAML alternative (Phase 3 candidate)

### dbt Models

- [ ] True cross-source spend model (all platforms unified)
- [ ] Benchmark model (anonymous cross-client comparison)
- [ ] Basic last-click attribution model
- [ ] `dbt docs generate` hosting

### Infrastructure

- [ ] Automated Postgres backups (cron + pg_dump, 30-day retention)
- [ ] Pipeline log rotation (logrotate)
- [ ] Metabase healthcheck endpoint
- [ ] Disaster recovery documentation

---

## Phase 3 - Scale

Evaluate when client count or data volume justifies it.

- [ ] Migrate from cron to Prefect Cloud / Dagster
- [ ] Date-partitioned raw tables for high-volume connectors
- [ ] Postgres read replica for Metabase workload isolation
- [ ] CI/CD for dbt model deployment on merge to main
- [ ] Re-evaluate Airbyte vs dlt if connectors > 20

---

## Design Decisions

| Decision | Outcome | Rationale |
|---|---|---|
| NocoDB | ❌ Not used | Metabase covers data inspection needs |
| dbt Cloud | ❌ Not used | Free tier risk, prefer full control |
| Prefect / Airflow | ⏳ Postponed to Phase 3 | Overengineering for MVP |
| Email alerts | ❌ Not used | Telegram API is simpler than email OAuth |
| Per-client database | ❌ Not used | Schema isolation is sufficient for MVP |
| Config in Postgres | ⏳ Postponed to Phase 3 | YAML + Git is more auditable for now |
