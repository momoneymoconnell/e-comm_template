-- One row per customer, with lifetime value and ordering behaviour.
--
-- Keyed on email, not user_id. Guest checkouts have no user_id at all, and
-- keying on it would drop them entirely -- in most shops a large share of
-- first purchases. Email is what actually identifies a buyer across a guest
-- order and a later account.
--
-- Privacy: this mart holds email addresses because customer analysis requires
-- them. It inherits the read restrictions of the DuckDB file it lives in, and
-- it deliberately carries nothing beyond order behaviour -- no addresses, no
-- credentials, no browsing history.

with orders as (
    select * from {{ ref('stg_orders') }}
),

users as (
    select * from {{ ref('stg_users') }}
),

by_customer as (
    select
        email,

        count(*)                                    as orders_total,
        count(*) filter (where is_revenue)          as orders,
        coalesce(sum(total_cents) filter (where is_revenue), 0) as lifetime_value_cents,

        min(order_date)                             as first_order_date,
        max(order_date)                             as last_order_date,
        bool_or(is_guest_order)                     as ever_ordered_as_guest,
        max(user_id)                                as user_id

    from orders
    group by email
)

select
    c.email,
    c.user_id,
    c.orders,
    c.orders_total,
    c.lifetime_value_cents,

    case
        when c.orders > 0 then cast(c.lifetime_value_cents / c.orders as bigint)
        else 0
    end                                         as avg_order_value_cents,

    c.orders > 1                                as is_repeat_customer,
    c.ever_ordered_as_guest,

    c.first_order_date,
    c.last_order_date,
    date_diff('day', c.last_order_date, current_date) as days_since_last_order,
    date_diff('day', c.first_order_date, c.last_order_date) as customer_lifespan_days,

    u.signup_date,
    u.is_email_verified,
    u.user_id is not null                       as has_account

from by_customer c
left join users u on u.email = c.email
order by c.lifetime_value_cents desc
