with source as (
    select * from {{ source('raw_facebook', 'page_insights_daily') }}
),

renamed as (
    select
        report_date::date                            as report_date,
        page_total_media_view_unique::bigint         as page_total_media_view_unique,
        page_media_view::bigint                      as page_media_view,
        page_video_views::bigint                     as page_video_views,
        page_views_total::bigint                     as page_views_total,
        page_daily_follows::bigint                   as page_daily_follows,
        page_total_actions::bigint                   as page_total_actions,
        page_follows::bigint                         as page_follows,
        page_post_engagements::bigint                as page_post_engagements,
        page_daily_follows_unique::bigint            as page_daily_follows_unique,
        page_daily_unfollows_unique::bigint          as page_daily_unfollows_unique,
        page_actions_post_reactions_like_total::bigint  as page_actions_post_reactions_like_total,
        page_actions_post_reactions_love_total::bigint  as page_actions_post_reactions_love_total,
        page_actions_post_reactions_wow_total::bigint   as page_actions_post_reactions_wow_total,
        page_actions_post_reactions_haha_total::bigint  as page_actions_post_reactions_haha_total,
        page_actions_post_reactions_sorry_total::bigint as page_actions_post_reactions_sorry_total,
        page_actions_post_reactions_anger_total::bigint as page_actions_post_reactions_anger_total
    from source
)

select * from renamed
