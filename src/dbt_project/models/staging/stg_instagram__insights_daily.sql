with source as (
    select * from {{ source('raw_instagram', 'insights_daily') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        reach::bigint                           as reach,
        follower_count::bigint                  as follower_count
    from source
)

select * from renamed
