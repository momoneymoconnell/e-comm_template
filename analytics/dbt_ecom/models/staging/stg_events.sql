-- Traffic events.
--
-- Already pseudonymised at ingest: no raw IP, no full user agent, no query
-- string. `visitor_hash` is salted with a salt that rotates daily, so it
-- identifies a visitor within a day and deliberately stops doing so after
-- that. Any model counting "unique visitors" over a longer window is therefore
-- counting visitor-days, which is the honest figure and is documented as such.

with source as (
    select * from {{ source('analytics', 'events') }}
),

renamed as (
    select
        id                              as event_id,
        event_type,
        session_hash,
        visitor_hash,
        user_id,

        path,
        referrer_host,
        device_type,
        browser_family,
        country,

        user_id is not null             as is_authenticated,
        referrer_host is null           as is_direct,

        cast(occurred_at as timestamp)  as occurred_at,
        cast(occurred_at as date)       as event_date

    from source
)

select * from renamed
