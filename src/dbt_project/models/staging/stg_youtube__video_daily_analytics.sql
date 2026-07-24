with source as (
    select * from {{ source('raw_youtube', 'video_daily_analytics') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        video_id::varchar                       as video_id,
        views::bigint                           as views,
        estimated_minutes_watched::numeric(15,2) as estimated_minutes_watched,
        average_view_duration_seconds::numeric(10,2) as average_view_duration_seconds
    from source
)

select * from renamed
