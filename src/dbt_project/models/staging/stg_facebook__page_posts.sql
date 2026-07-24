with source as (
    select * from {{ source('raw_facebook', 'page_posts') }}
),

renamed as (
    select
        post_id::varchar                        as post_id,
        message::varchar                        as message,
        created_time::timestamp                 as created_time,
        permalink_url::varchar                  as permalink_url,
        story::varchar                          as story,
        likes_count::bigint                     as likes_count,
        comments_count::bigint                  as comments_count,
        shares_count::bigint                    as shares_count
    from source
)

select * from renamed
