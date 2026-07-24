with source as (
    select * from {{ source('raw_google', 'ads') }}
),

renamed as (
    select
        ad_id::varchar                   as ad_id,
        ad_group_id::varchar             as ad_group_id,
        campaign_id::varchar             as campaign_id,
        ad_name::varchar                 as ad_name,
        status::varchar                  as status,
        spend_usd::numeric(15,2)         as spend_usd,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        ctr::numeric(10,4)               as ctr,
        average_cpc::numeric(10,2)       as average_cpc,
        conversions::numeric(10,2)       as conversions,
        cost_per_conversion::numeric(10,2) as cost_per_conversion,
        date::date                       as report_date,
        _dlt_load_id::varchar            as _dlt_load_id,
        _dlt_id::varchar                 as _dlt_id
    from source
)

select * from renamed
