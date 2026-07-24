with source as (
    select * from {{ source('raw_ga4', 'daily_stats') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        sessions::bigint                        as sessions,
        total_users::bigint                     as total_users,
        new_users::bigint                       as new_users,
        pageviews::bigint                       as pageviews,
        bounce_rate::numeric(10,4)              as bounce_rate,
        avg_session_duration_seconds::numeric(10,2) as avg_session_duration_seconds
    from source
)

select * from renamed
