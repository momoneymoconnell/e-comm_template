-- Daily traffic.
--
-- "Unique visitors" is counted from `visitor_hash`, whose salt rotates daily.
-- Within one day it counts people; summed across days it counts visitor-days,
-- because the same person hashes differently tomorrow. That is a deliberate
-- privacy trade, and it is stated here so nobody later builds a "monthly
-- uniques" figure on top of it and believes it means something it does not.

with events as (
    select * from {{ ref('stg_events') }}
),

date_spine as (
    select cast(unnest(generate_series(
        (select min(event_date) from events),
        current_date,
        interval 1 day
    )) as date) as day
),

daily as (
    select
        event_date                                             as day,
        count(*) filter (where event_type = 'page_view')        as page_views,
        count(distinct visitor_hash)                            as visitors,
        count(distinct session_hash)                            as sessions,
        count(*) filter (where event_type = 'product_view')     as product_views,
        count(*) filter (where event_type = 'add_to_cart')      as add_to_carts,
        count(*) filter (where event_type = 'checkout_started') as checkouts_started,
        count(*) filter (where event_type = 'checkout_completed') as checkouts_completed,
        count(distinct session_hash) filter (where device_type = 'mobile')  as mobile_sessions,
        count(distinct session_hash) filter (where device_type = 'desktop') as desktop_sessions,
        count(distinct session_hash) filter (where is_direct)    as direct_sessions
    from events
    group by event_date
)

select
    s.day,
    coalesce(d.page_views, 0)           as page_views,
    coalesce(d.visitors, 0)             as visitors,
    coalesce(d.sessions, 0)             as sessions,
    coalesce(d.product_views, 0)        as product_views,
    coalesce(d.add_to_carts, 0)         as add_to_carts,
    coalesce(d.checkouts_started, 0)    as checkouts_started,
    coalesce(d.checkouts_completed, 0)  as checkouts_completed,
    coalesce(d.mobile_sessions, 0)      as mobile_sessions,
    coalesce(d.desktop_sessions, 0)     as desktop_sessions,
    coalesce(d.direct_sessions, 0)      as direct_sessions,

    case
        when coalesce(d.sessions, 0) > 0
            then round(d.checkouts_completed * 100.0 / d.sessions, 2)
        else 0
    end                                 as conversion_rate_pct

from date_spine s
left join daily d on d.day = s.day
order by s.day
