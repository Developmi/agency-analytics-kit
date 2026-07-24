with source as (
    select * from {{ source('raw_google', 'campaigns') }}
),

renamed as (
    select
        campaign_id::varchar             as campaign_id,
        campaign_name::varchar           as campaign_name,
        status::varchar                  as status,
        advertising_channel_type::varchar as advertising_channel_type,
        budget::numeric(15,2)            as budget,
        budget_type::varchar             as budget_type,
        spend_usd::numeric(15,2)         as spend_usd,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        ctr::numeric(10,4)               as ctr,
        average_cpc::numeric(10,2)       as average_cpc,
        conversions::numeric(10,2)       as conversions,
        cost_per_conversion::numeric(10,2) as cost_per_conversion,
        start_date::date                 as start_date,
        end_date::date                   as end_date,
        date::date                       as report_date,
        _dlt_load_id::varchar            as _dlt_load_id,
        _dlt_id::varchar                 as _dlt_id
    from source
)

select * from renamed
