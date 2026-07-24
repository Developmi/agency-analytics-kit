with source as (
    select * from {{ source('raw_instagram', 'media') }}
),

renamed as (
    select
        media_id::varchar                       as media_id,
        caption::varchar                        as caption,
        media_type::varchar                     as media_type,
        permalink::varchar                      as permalink,
        like_count::bigint                      as like_count,
        comments_count::bigint                  as comments_count,
        timestamp::timestamp                    as timestamp
    from source
)

select * from renamed
