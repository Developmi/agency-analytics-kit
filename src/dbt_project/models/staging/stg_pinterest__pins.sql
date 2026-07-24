with source as (
    select * from {{ source('raw_pinterest', 'pins') }}
),

renamed as (
    select
        pin_id::varchar                         as pin_id,
        title::varchar                          as title,
        description::varchar                    as description,
        link::varchar                           as link,
        destination_url::varchar                as destination_url,
        pin_count::bigint                       as pin_count,
        save_count::bigint                      as save_count,
        created_at::timestamp                   as created_at
    from source
)

select * from renamed
