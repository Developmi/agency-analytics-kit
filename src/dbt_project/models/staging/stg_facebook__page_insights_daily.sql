with source as (
    select * from {{ source('raw_facebook', 'page_insights_daily') }}
),

renamed as (
    select
        report_date::date                        as report_date,
        page_total_media_view_unique::bigint     as page_total_media_view_unique,
        page_media_view::bigint                  as page_media_view,
        page_video_views::bigint                 as page_video_views,
        page_views_total::bigint                 as page_views_total,
        page_daily_follows::bigint               as page_daily_follows,
        page_total_actions::bigint               as page_total_actions
    from source
)

select * from renamed
