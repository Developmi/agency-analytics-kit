with source as (
    select * from {{ source('raw_youtube', 'videos') }}
),

renamed as (
    select
        video_id::varchar                       as video_id,
        title::varchar                          as title,
        published_at::timestamp                 as published_at,
        view_count::bigint                      as view_count,
        like_count::bigint                      as like_count,
        comment_count::bigint                   as comment_count,
        duration::varchar                       as duration,
        category_id::varchar                    as category_id
    from source
)

select * from renamed
