-- Agency Analytics Kit - Pipeline observability tables
-- Auto-executed by Postgres on first container start (docker-entrypoint-initdb.d)

CREATE SCHEMA IF NOT EXISTS public;

CREATE TABLE IF NOT EXISTS public.pipeline_runs (
    id              BIGSERIAL PRIMARY KEY,
    client_id       VARCHAR(255) NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(50) NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'success', 'failed')),
    connectors_total INT NOT NULL DEFAULT 0,
    connectors_ok   INT NOT NULL DEFAULT 0,
    connectors_failed INT NOT NULL DEFAULT 0,
    dbt_status      VARCHAR(50),
    dbt_duration_ms INT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.pipeline_run_steps (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES public.pipeline_runs(id) ON DELETE CASCADE,
    step_name       VARCHAR(255) NOT NULL,
    connector       VARCHAR(100),
    status          VARCHAR(50) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'running', 'success', 'failed', 'skipped')),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    duration_ms     INT,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_client_id   ON public.pipeline_runs(client_id);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started_at  ON public.pipeline_runs(started_at);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status       ON public.pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_run_steps_run_id  ON public.pipeline_run_steps(run_id);
