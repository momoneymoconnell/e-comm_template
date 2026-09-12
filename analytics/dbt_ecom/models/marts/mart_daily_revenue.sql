-- Daily orders and revenue. The dashboard's headline chart.
--
-- Built from a generated date spine rather than from the orders themselves.
-- Grouping orders alone omits days with no sales entirely, and a line chart
-- that skips those days draws a straight line across a quiet week -- which
-- looks like steady trading rather than none at all.

with orders as (
    select * from {{ ref('stg_orders') }}
),

-- Every day from the first order to today, so gaps are explicit zeros.
date_spine as (
    select cast(unnest(generate_series(
        (select min(order_date) from orders),
        current_date,
        interval 1 day
    )) as date) as order_day
),

daily as (
    select
        order_date                                          as order_day,
        count(*)                                            as orders_placed,
        count(*) filter (where is_revenue)                  as orders_paid,
        count(*) filter (where status = 'cancelled')        as orders_cancelled,
        count(*) filter (where is_refunded)                 as orders_refunded,
        coalesce(sum(total_cents)    filter (where is_revenue), 0) as revenue_cents,
        coalesce(sum(subtotal_cents) filter (where is_revenue), 0) as subtotal_cents,
        coalesce(sum(shipping_cents) filter (where is_revenue), 0) as shipping_cents,
        coalesce(sum(tax_cents)      filter (where is_revenue), 0) as tax_cents,
        count(distinct email) filter (where is_revenue)      as paying_customers
    from orders
    group by order_date
)

select
    s.order_day,
    coalesce(d.orders_placed, 0)     as orders_placed,
    coalesce(d.orders_paid, 0)       as orders,
    coalesce(d.orders_cancelled, 0)  as orders_cancelled,
    coalesce(d.orders_refunded, 0)   as orders_refunded,
    coalesce(d.revenue_cents, 0)     as revenue_cents,
    coalesce(d.subtotal_cents, 0)    as subtotal_cents,
    coalesce(d.shipping_cents, 0)    as shipping_cents,
    coalesce(d.tax_cents, 0)         as tax_cents,
    coalesce(d.paying_customers, 0)  as paying_customers,

    -- Guarded against division by zero: a day with no paid orders has an
    -- average order value of zero, not an error that fails the whole run.
    case
        when coalesce(d.orders_paid, 0) > 0
            then cast(d.revenue_cents / d.orders_paid as bigint)
        else 0
    end                              as avg_order_value_cents

from date_spine s
left join daily d on d.order_day = s.order_day
order by s.order_day
