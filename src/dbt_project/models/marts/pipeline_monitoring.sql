{{ config(materialized='table', tags=['marts', var('client_id')]) }}

with runs as (
    select * from {{ ref('stg_public__pipeline_runs') }}
),

steps as (
    select * from {{ ref('stg_public__pipeline_run_steps') }}
),

latest_runs as (
    select
        run_id,
        client_id,
        started_at,
        finished_at,
        run_status,
        connectors_total,
        connectors_ok,
        connectors_failed,
        dbt_status,
        dbt_duration_ms,
        error_message,
        row_number() over (
            partition by client_id
            order by started_at desc
        ) as rn
    from runs
),

latest_5_per_client as (
    select *
    from latest_runs
    where rn <= 5
),

step_summary as (
    select
        s.run_id,
        count(*)                                          as total_steps,
        count(*) filter (where s.step_status = 'success') as steps_ok,
        count(*) filter (where s.step_status = 'failed')  as steps_failed,
        count(*) filter (where s.step_status = 'skipped') as steps_skipped,
        max(s.duration_ms)                                as max_step_duration_ms
    from steps s
    group by s.run_id
)

select
    r.client_id,
    r.run_id,
    r.started_at,
    r.finished_at,
    r.run_status,
    r.connectors_total,
    r.connectors_ok,
    r.connectors_failed,
    coalesce(r.dbt_status, 'skipped') as dbt_status,
    r.dbt_duration_ms,
    coalesce(s.total_steps, 0)        as total_steps,
    coalesce(s.steps_ok, 0)           as steps_ok,
    coalesce(s.steps_failed, 0)       as steps_failed,
    coalesce(s.steps_skipped, 0)      as steps_skipped,
    case
        when r.run_status = 'success' then '✅'
        when r.run_status = 'failed'  then '❌'
        else '🔄'
    end as status_icon
from latest_5_per_client r
left join step_summary s on r.run_id = s.run_id
order by r.client_id, r.started_at desc
