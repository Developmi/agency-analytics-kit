with source as (
    select * from {{ source('raw_gtm', 'containers') }}
),

renamed as (
    select
        container_id::varchar                   as container_id,
        account_id::varchar                     as account_id,
        name::varchar                           as name,
        public_id::varchar                      as public_id,
        usage_context::varchar                  as usage_context
    from source
)

select * from renamed
