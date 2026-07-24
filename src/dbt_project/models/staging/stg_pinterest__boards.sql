with source as (
    select * from {{ source('raw_pinterest', 'boards') }}
),

renamed as (
    select
        board_id::varchar                       as board_id,
        name::varchar                           as name,
        description::varchar                    as description,
        pin_count::bigint                       as pin_count,
        follower_count::bigint                  as follower_count,
        created_at::timestamp                   as created_at
    from source
)

select * from renamed
