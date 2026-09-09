{{ config(tags=['marts', var('client_id')]) }}

/*
    organic_instagram_daily — daily IG reach/follower mart (ORG-R2).

    Consumer contract:
    * Grain: one row per (client_id, report_date); a row exists when EITHER
      source holds the date.
    * Content: the live daily set (stg_instagram__insights_daily — reach for
      <=729d, follower only inside the 30d live horizon) merged FULL OUTER
      with the append-only follower archive
      (raw_instagram.follower_count_history) on report_date.
    * reach comes from live only and is NULL on history-only days (ORG-S5).
    * follower = COALESCE(live.follower_count, archived.follower_count): the
      live NULL marker means "no follower data for that day in the live
      horizon", never 0, and never shadows an archived value for dates beyond
      the 30d live horizon (ORG-S4).
*/
with live as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        report_date,
        reach,
        follower_count
    from {{ ref('stg_instagram__insights_daily') }}
),

hist as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        report_date,
        follower_count
    from {{ source('raw_instagram', 'follower_count_history') }}
)

select
    coalesce(live.client_id, hist.client_id)               as client_id,
    coalesce(live.report_date, hist.report_date)           as report_date,
    live.reach                                             as reach,
    coalesce(live.follower_count, hist.follower_count)     as follower_count
from live
full outer join hist
    on live.report_date = hist.report_date
