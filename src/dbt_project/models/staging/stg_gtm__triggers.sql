with source as (
    select * from {{ source('raw_gtm', 'triggers') }}
),

renamed as (
    select
        trigger_id::varchar                     as trigger_id,
        container_id::varchar                   as container_id,
        type::varchar                           as type,
        name::varchar                           as name,
        filter_json::json                       as filter_json
    from source
)

select * from renamed
