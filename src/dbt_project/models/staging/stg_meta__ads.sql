with source as (
    select * from {{ source('raw_meta', 'ads') }}
),

renamed as (
    select
        ad_id::varchar                   as ad_id,
        ad_name::varchar                 as ad_name,
        status::varchar                  as status,
        spend::numeric(15,2)             as spend,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        reach::bigint                    as reach,
        frequency::numeric(10,2)         as frequency,
        cpm::numeric(10,2)               as cpm,
        cpc::numeric(10,2)               as cpc,
        date::date                       as report_date,
        _dlt_load_id::varchar            as _dlt_load_id,
        _dlt_id::varchar                 as _dlt_id
    from source
)

select * from renamed
