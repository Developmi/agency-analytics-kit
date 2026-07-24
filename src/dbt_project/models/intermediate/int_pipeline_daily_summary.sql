{{ config(materialized='view', tags=['intermediate', var('client_id')]) }}

with runs as (
    select * from {{ ref('stg_public__pipeline_runs') }}
),

daily as (
    select
        date(started_at)                    as report_date,
        client_id,
        count(*)                            as total_runs,
        count(*) filter (where run_status = 'success') as successful_runs,
        count(*) filter (where run_status = 'failed')  as failed_runs,
        avg(connectors_ok)                  as avg_connectors_ok,
        avg(connectors_failed)              as avg_connectors_failed,
        avg(dbt_duration_ms)                as avg_dbt_duration_ms,
        sum(connectors_ok)                  as total_connectors_ok,
        sum(connectors_failed)              as total_connectors_failed
    from runs
    group by 1, 2
)

select * from daily
