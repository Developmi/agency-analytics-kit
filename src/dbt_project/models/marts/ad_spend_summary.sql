{{ config(tags=['marts', var('client_id')]) }}

select
    source_platform                  as platform,
    report_date,
    sum(spend)                       as total_spend,
    sum(impressions)                 as total_impressions,
    sum(clicks)                      as total_clicks,
    count(distinct campaign_id)      as campaign_count
from {{ ref('int_unified_spend') }}
where client_schema = '{{ var("client_schema") }}'
group by source_platform, report_date
order by report_date, source_platform
