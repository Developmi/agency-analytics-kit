{{ config(materialized='view') }}

with source as (
    select * from {{ source('public', 'pipeline_run_steps') }}
),

renamed as (
    select
        id::bigint                          as step_id,
        run_id::bigint                      as run_id,
        step_name::varchar                  as step_name,
        connector::varchar                  as connector,
        status::varchar                     as step_status,
        started_at::timestamptz             as started_at,
        finished_at::timestamptz            as finished_at,
        duration_ms::int                    as duration_ms,
        error_message::text                 as error_message,
        created_at::timestamptz             as created_at
    from source
)

select * from renamed
