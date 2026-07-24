{{ config(tags=['marts', var('client_id')]) }}

select
    source_platform                  as platform,
    campaign_id,
    campaign_name,
    report_date,
    sum(spend)                       as total_spend,
    sum(impressions)                 as total_impressions,
    sum(clicks)                      as total_clicks,
    round(
        sum(clicks)::numeric / nullif(sum(impressions), 0) * 100,
        4
    )                                as ctr,
    round(
        sum(spend) / nullif(sum(clicks), 0),
        2
    )                                as cpc,
    round(
        sum(spend) / nullif(sum(impressions), 0) * 1000,
        2
    )                                as cpm
from {{ ref('int_unified_spend') }}
where client_schema = '{{ var("client_schema") }}'
group by source_platform, campaign_id, campaign_name, report_date
