with source as (
    select * from {{ source('raw_tiktok_organic', 'profile_stats') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        follower_count::bigint                  as follower_count,
        following_count::bigint                 as following_count,
        likes_count::bigint                     as likes_count,
        video_count::bigint                     as video_count
    from source
)

select * from renamed
