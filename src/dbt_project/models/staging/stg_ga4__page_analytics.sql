with source as (
    select * from {{ source('raw_ga4', 'page_analytics') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        page_path::varchar                      as page_path,
        page_title::varchar                     as page_title,
        pageviews::bigint                       as pageviews,
        unique_pageviews::bigint                as unique_pageviews,
        avg_time_on_page_seconds::numeric(10,2) as avg_time_on_page_seconds,
        bounce_rate::numeric(10,4)              as bounce_rate
    from source
)

select * from renamed
