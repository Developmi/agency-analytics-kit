with source as (
    select * from {{ source('raw_tiktok_organic', 'videos_organic') }}
),

renamed as (
    select
        video_id::varchar                       as video_id,
        title::varchar                          as title,
        to_timestamp(create_time::bigint)        as create_time,
        like_count::bigint                      as like_count,
        comment_count::bigint                   as comment_count,
        share_count::bigint                     as share_count,
        view_count::bigint                      as view_count
    from source
)

select * from renamed
