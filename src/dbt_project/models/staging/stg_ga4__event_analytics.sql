with source as (
    select * from {{ source('raw_ga4', 'event_analytics') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        event_name::varchar                     as event_name,
        event_count::bigint                     as event_count,
        user_count::bigint                      as user_count
    from source
)

select * from renamed
