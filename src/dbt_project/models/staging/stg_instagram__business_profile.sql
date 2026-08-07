with source as (
    select * from {{ source('raw_instagram', 'business_profile') }}
),

renamed as (
    select
        ig_id::varchar                          as ig_id,
        username::varchar                       as username,
        name::varchar                           as name,
        profile_picture_url::varchar            as profile_picture_url,
        biography::varchar                      as biography,
        website::varchar                        as website,
        followers_count::bigint                 as followers_count,
        follows_count::bigint                   as follows_count,
        media_count::bigint                     as media_count
    from source
)

select * from renamed
