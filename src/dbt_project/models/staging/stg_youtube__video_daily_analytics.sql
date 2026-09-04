with source as (
    select * from {{ source('raw_youtube', 'video_daily_analytics') }}
),

renamed as (
    select
        report_date::date                               as report_date,
        views::bigint                                   as views,
        estimated_minutes_watched::numeric(15,2)        as estimated_minutes_watched,
        average_view_duration::numeric(10,2)            as average_view_duration,
        average_view_percentage::numeric(10,2)          as average_view_percentage,
        subscribers_gained::bigint                      as subscribers_gained,
        subscribers_lost::bigint                        as subscribers_lost,
        likes::bigint                                   as likes,
        dislikes::bigint                                as dislikes,
        comments::bigint                                   as comments,
        shares::bigint                                     as shares,
        engaged_views::bigint                              as engaged_views
    from source
)

select * from renamed
