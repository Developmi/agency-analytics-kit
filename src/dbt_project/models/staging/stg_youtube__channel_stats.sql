with source as (
    select * from {{ source('raw_youtube', 'channel_stats') }}
),

renamed as (
    select
        report_date::date                       as report_date,
        subscriber_count::bigint                as subscriber_count,
        view_count::bigint                      as view_count,
        video_count::bigint                     as video_count,
        hidden_subscriber_count::boolean        as hidden_subscriber_count
    from source
)

select * from renamed
