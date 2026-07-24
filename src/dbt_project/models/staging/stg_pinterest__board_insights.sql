with source as (
    select * from {{ source('raw_pinterest', 'board_insights') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        board_id::varchar                       as board_id,
        reach::bigint                           as reach,
        impressions::bigint                     as impressions,
        saves::bigint                           as saves,
        clicks::bigint                          as clicks
    from source
)

select * from renamed
