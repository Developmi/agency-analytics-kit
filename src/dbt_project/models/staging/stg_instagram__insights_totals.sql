with source as (
    select * from {{ source('raw_instagram', 'insights_totals') }}
),

renamed as (
    select
        date_start::date                    as date_start,
        date_end::date                      as date_end,
        views::bigint                       as views,
        likes::bigint                       as likes,
        comments::bigint                    as comments,
        shares::bigint                      as shares,
        saves::bigint                       as saves,
        total_interactions::bigint          as total_interactions,
        accounts_engaged::bigint            as accounts_engaged,
        replies::bigint                     as replies,
        reposts::bigint                     as reposts,
        follows_and_unfollows::bigint       as follows_and_unfollows,
        profile_links_taps::bigint          as profile_links_taps
    from source
)

select * from renamed
