with source as (
    select * from {{ source('raw_tiktok', 'campaigns') }}
),

renamed as (
    select
        campaign_id::varchar             as campaign_id,
        campaign_name::varchar           as campaign_name,
        status::varchar                  as status,
        objective::varchar               as objective,
        budget::numeric(15,2)            as budget,
        budget_type::varchar             as budget_type,
        spend::numeric(15,2)             as spend,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        reach::bigint                    as reach,
        start_date::date                 as start_date,
        end_date::date                   as end_date,
        date::date                       as report_date,
        _dlt_load_id::varchar            as _dlt_load_id,
        _dlt_id::varchar                 as _dlt_id
    from source
)

select * from renamed
