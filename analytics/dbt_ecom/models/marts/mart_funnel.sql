-- The checkout funnel, counted on distinct sessions.
--
-- Sessions, not events, and this is the whole point. A shopper who adds three
-- items is one session that reached "add to cart", not three. Counting raw
-- events makes the funnel widen partway down, which is impossible and makes
-- the chart meaningless.

with events as (
    select * from {{ ref('stg_events') }}
),

steps as (
    select 1 as step_order, 'page_view'          as step, 'Visited the site'  as label
    union all select 2, 'product_view',       'Viewed a product'
    union all select 3, 'add_to_cart',        'Added to cart'
    union all select 4, 'checkout_started',   'Started checkout'
    union all select 5, 'checkout_completed', 'Completed checkout'
),

counted as (
    select
        event_type,
        count(distinct session_hash) as sessions
    from events
    group by event_type
),

joined as (
    select
        s.step_order,
        s.step,
        s.label,
        coalesce(c.sessions, 0) as sessions
    from steps s
    left join counted c on c.event_type = s.step
)

select
    step_order,
    step,
    label,
    sessions,

    -- Share of everyone who arrived.
    case
        when max(sessions) over () > 0
            then round(sessions * 100.0 / max(sessions) over (), 2)
        else 0
    end as pct_of_visitors,

    -- Share of the previous step, which is where the actual drop-off shows.
    case
        when lag(sessions) over (order by step_order) > 0
            then round(sessions * 100.0 / lag(sessions) over (order by step_order), 2)
        else 100.0
    end as pct_of_previous_step

from joined
order by step_order
