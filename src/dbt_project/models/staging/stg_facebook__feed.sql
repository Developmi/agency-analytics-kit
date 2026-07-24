with source as (
    select * from {{ source('raw_facebook', 'feed') }}
),

renamed as (
    select
        feed_item_id::varchar                        as feed_item_id,
        message::text                                as message,
        created_time::timestamptz                    as created_time,
        permalink_url::varchar                       as permalink_url,
        story::varchar                               as story,
        author_id::varchar                           as author_id,
        author_name::varchar                         as author_name,
        likes_count::bigint                          as likes_count,
        comments_count::bigint                       as comments_count,
        shares_count::bigint                         as shares_count
    from source
)

select * from renamed
