with source as (
    select * from {{ source('raw_tiktok_organic', 'profile_stats') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        follower_count::bigint                  as follower_count,
        following_count::bigint                 as following_count,
        total_likes::bigint                     as total_likes,
        total_videos::bigint                    as total_videos
    from source
)

select * from renamed
