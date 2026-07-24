{{ config(materialized='view') }}

with source as (
    select * from {{ source('public', 'pipeline_runs') }}
),

renamed as (
    select
        id::bigint                         as run_id,
        client_id::varchar                  as client_id,
        started_at::timestamptz             as started_at,
        finished_at::timestamptz            as finished_at,
        status::varchar                     as run_status,
        connectors_total::int               as connectors_total,
        connectors_ok::int                  as connectors_ok,
        connectors_failed::int              as connectors_failed,
        dbt_status::varchar                 as dbt_status,
        dbt_duration_ms::int                as dbt_duration_ms,
        error_message::text                 as error_message,
        created_at::timestamptz             as created_at
    from source
)

select * from renamed
