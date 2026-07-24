{{ config(tags=['intermediate', var('client_id')]) }}

with meta_ads as (
    select
        'Meta'::varchar                  as source_platform,
        null::varchar                    as campaign_id,
        null::varchar                    as campaign_name,
        ad_id::varchar                   as ad_id,
        ad_name::varchar                 as ad_name,
        spend::numeric(15,2)             as spend,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        report_date::date                as report_date,
        '{{ var("client_schema") }}'::varchar as client_schema
    from {{ ref('stg_meta__ads') }}
),

tiktok_ads as (
    select
        'TikTok'::varchar                as source_platform,
        null::varchar                    as campaign_id,
        null::varchar                    as campaign_name,
        ad_id::varchar                   as ad_id,
        ad_name::varchar                 as ad_name,
        spend::numeric(15,2)             as spend,
        impressions::bigint              as impressions,
        clicks::bigint                   as clicks,
        report_date::date                as report_date,
        '{{ var("client_schema") }}'::varchar as client_schema
    from {{ ref('stg_tiktok__ads') }}
),

google_ads as (
    select
        'Google'::varchar                as source_platform,
        stg_ads.campaign_id::varchar     as campaign_id,
        stg_campaigns.campaign_name::varchar as campaign_name,
        stg_ads.ad_id::varchar           as ad_id,
        stg_ads.ad_name::varchar         as ad_name,
        stg_ads.spend_usd::numeric(15,2) as spend,
        stg_ads.impressions::bigint      as impressions,
        stg_ads.clicks::bigint           as clicks,
        stg_ads.report_date::date        as report_date,
        '{{ var("client_schema") }}'::varchar as client_schema
    from {{ ref('stg_google__ads') }} stg_ads
    left join {{ ref('stg_google__campaigns') }} stg_campaigns
        on stg_ads.campaign_id = stg_campaigns.campaign_id
)

select * from meta_ads
union all
select * from tiktok_ads
union all
select * from google_ads
