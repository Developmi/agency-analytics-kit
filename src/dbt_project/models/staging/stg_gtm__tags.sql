with source as (
    select * from {{ source('raw_gtm', 'tags') }}
),

renamed as (
    select
        tag_id::varchar                         as tag_id,
        container_id::varchar                   as container_id,
        type::varchar                           as type,
        name::varchar                           as name,
        firing_triggers::json                   as firing_triggers,
        blocking_triggers::json                 as blocking_triggers,
        tag_manager_url::varchar                as tag_manager_url
    from source
)

select * from renamed
