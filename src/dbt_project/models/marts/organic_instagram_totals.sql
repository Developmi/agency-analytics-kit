{{ config(tags=['marts', var('client_id')]) }}

/*
    organic_instagram_totals — window-grain IG totals mart (ORG-R1).

    Consumer contract:
    * Grain: one row per totals window (client_id, date_start, date_end) plus a
      provenance label. Window rows are NEVER expanded to daily rows.
    * Content: the live serving set (stg_instagram__insights_totals) merged
      FULL OUTER with the append-only archive
      (raw_instagram.insights_totals_history) on the window key; a live row
      wins on a same-key duplicate. History supplies windows already evicted
      (>90d) from live so historical trend depth survives live replacement.
    * window_label is presence-based, never date arithmetic / CURRENT_DATE:
      'recency' when the live set serves the window (the winning row is the
      live row), 'archived' when only history holds the window.
    * The 9 common metrics COALESCE(live, history). The 2 gated metrics
      (follows_and_unfollows, profile_links_taps) are served only for the
      current window (max date_end) and are NULL elsewhere — gated values are
      never fabricated from archived history (ORG-S3).
*/
with live as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        date_start,
        date_end,
        views,
        likes,
        comments,
        shares,
        saves,
        total_interactions,
        accounts_engaged,
        replies,
        reposts,
        follows_and_unfollows,
        profile_links_taps
    from {{ ref('stg_instagram__insights_totals') }}
),

hist as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        date_start,
        date_end,
        views,
        likes,
        comments,
        shares,
        saves,
        total_interactions,
        accounts_engaged,
        replies,
        reposts,
        follows_and_unfollows,
        profile_links_taps
    from {{ source('raw_instagram', 'insights_totals_history') }}
)

select
    coalesce(live.client_id, hist.client_id)                   as client_id,
    coalesce(live.date_start, hist.date_start)                 as date_start,
    coalesce(live.date_end, hist.date_end)                     as date_end,
    case
        when live.date_start is not null then 'recency'
        else 'archived'
    end                                                        as window_label,
    coalesce(live.views, hist.views)                           as views,
    coalesce(live.likes, hist.likes)                           as likes,
    coalesce(live.comments, hist.comments)                     as comments,
    coalesce(live.shares, hist.shares)                         as shares,
    coalesce(live.saves, hist.saves)                           as saves,
    coalesce(live.total_interactions, hist.total_interactions) as total_interactions,
    coalesce(live.accounts_engaged, hist.accounts_engaged)     as accounts_engaged,
    coalesce(live.replies, hist.replies)                       as replies,
    coalesce(live.reposts, hist.reposts)                       as reposts,
    case
        when coalesce(live.date_end, hist.date_end) = max(coalesce(live.date_end, hist.date_end)) over ()
            then coalesce(live.follows_and_unfollows, hist.follows_and_unfollows)
    end                                                        as follows_and_unfollows,
    case
        when coalesce(live.date_end, hist.date_end) = max(coalesce(live.date_end, hist.date_end)) over ()
            then coalesce(live.profile_links_taps, hist.profile_links_taps)
    end                                                        as profile_links_taps
from live
full outer join hist
    on live.date_start = hist.date_start
    and live.date_end = hist.date_end
