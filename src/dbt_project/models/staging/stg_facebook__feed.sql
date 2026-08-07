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
        full_picture::varchar                        as full_picture,
        r_like::bigint                               as r_like,
        r_love::bigint                               as r_love,
        r_wow::bigint                                as r_wow,
        r_haha::bigint                               as r_haha,
        r_sad::bigint                                as r_sad,
        r_angry::bigint                              as r_angry,
        status_type::varchar                         as status_type,
        is_published::boolean                        as is_published,
        updated_time::timestamptz                    as updated_time,
        likes_count::bigint                          as likes_count,
        comments_count::bigint                       as comments_count,
        shares_count::bigint                         as shares_count
    from source
)

select * from renamed
