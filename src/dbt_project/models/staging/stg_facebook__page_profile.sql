with source as (
    select * from {{ source('raw_facebook', 'page_profile') }}
),

renamed as (
    select
        page_id::varchar                        as page_id,
        fan_count::bigint                       as fan_count,
        followers_count::bigint                 as followers_count,
        name::varchar                           as name,
        username::varchar                       as username,
        picture_url::varchar                    as picture_url,
        about::text                             as about,
        website::varchar                        as website,
        verification_status::varchar            as verification_status,
        rating_count::bigint                    as rating_count,
        category::varchar                       as category,
        cover::varchar                          as cover
    from source
)

select * from renamed
