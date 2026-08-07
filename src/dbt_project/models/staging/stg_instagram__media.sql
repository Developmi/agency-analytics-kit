with source as (
    select * from {{ source('raw_instagram', 'media') }}
),

renamed as (
    select
        media_id::varchar                       as media_id,
        caption::varchar                        as caption,
        media_type::varchar                     as media_type,
        media_url::varchar                      as media_url,
        permalink::varchar                      as permalink,
        thumbnail_url::varchar                  as thumbnail_url,
        shortcode::varchar                      as shortcode,
        media_product_type::varchar             as media_product_type,
        owner_id::varchar                       as owner_id,
        is_comment_enabled::boolean             as is_comment_enabled,
        like_count::bigint                      as like_count,
        comments_count::bigint                  as comments_count,
        "timestamp"::timestamp                  as timestamp
    from source
)

select * from renamed
