{{ config(tags=['marts', var('client_id')]) }}

/*
    organic_tiktok_profile_daily — daily TikTok organic profile mart (ORG-R3).

    Consumer contract:
    * Grain: one row per run day (client_id, report_date) exposing the 4
      profile counts (follower_count, following_count, likes_count,
      video_count).
    * Content: the live profile set (stg_tiktok_organic__profile_stats — latest
      run only) merged FULL OUTER with the append-only archive
      (raw_tiktok_organic.profile_stats_history) on report_date. Counts
      COALESCE(live, history), so a live row wins a same-day duplicate (values
      are identical on a re-run).
    * ARC-R6 capture-gap limitation: series depth equals RUN DAYS ONLY — days
      without a pipeline run are absent from the mart (never zero-filled).
      Dashboards must not interpret a missing day as zero.
*/
with live as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        report_date,
        follower_count,
        following_count,
        likes_count,
        video_count
    from {{ ref('stg_tiktok_organic__profile_stats') }}
),

hist as (
    select
        '{{ var('client_id') }}'::varchar as client_id,
        report_date,
        follower_count,
        following_count,
        likes_count,
        video_count
    from {{ source('raw_tiktok_organic', 'profile_stats_history') }}
)

select
    coalesce(live.client_id, hist.client_id)             as client_id,
    coalesce(live.report_date, hist.report_date)         as report_date,
    coalesce(live.follower_count, hist.follower_count)   as follower_count,
    coalesce(live.following_count, hist.following_count) as following_count,
    coalesce(live.likes_count, hist.likes_count)         as likes_count,
    coalesce(live.video_count, hist.video_count)         as video_count
from live
full outer join hist
    on live.report_date = hist.report_date
