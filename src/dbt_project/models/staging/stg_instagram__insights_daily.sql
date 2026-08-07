with source as (
    select * from {{ source('raw_instagram', 'insights_daily') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        reach::bigint                           as reach,
        views::bigint                           as views,
        profile_views::bigint                   as profile_views,
        follower_count::bigint                  as follower_count,
        likes::bigint                           as likes,
        comments::bigint                        as comments,
        shares::bigint                          as shares,
        saves::bigint                           as saves,
        total_interactions::bigint              as total_interactions,
        accounts_engaged::bigint                as accounts_engaged,
        website_clicks::bigint                  as website_clicks
    from source
)

select * from renamed
