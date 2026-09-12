-- Orders, cleaned and typed for analysis.
--
-- The `is_revenue` flag is the important part of this model. "Revenue" has to
-- mean exactly one thing across every dashboard, and the temptation is to
-- decide it separately in each mart -- at which point the finance report and
-- the product report disagree and nobody can say which is right.
--
-- Defined once, here: an order counts as revenue when payment has actually
-- been confirmed. Pending orders have not been paid for, and cancelled and
-- refunded ones are money the business does not have.

with source as (
    select * from {{ source('orders', 'orders') }}
),

renamed as (
    select
        id                                  as order_id,
        order_number,
        user_id,
        lower(email)                        as email,
        status,

        subtotal_cents,
        tax_cents,
        shipping_cents,
        total_cents,
        lower(currency)                     as currency,

        -- The single definition of revenue for the whole project.
        status in ('paid', 'fulfilled', 'delivered')  as is_revenue,
        status = 'refunded'                           as is_refunded,
        user_id is null                               as is_guest_order,

        cast(created_at as timestamp)       as created_at,
        cast(placed_at  as timestamp)       as placed_at,
        cast(paid_at    as timestamp)       as paid_at,
        cast(created_at as date)            as order_date

    from source
)

select * from renamed
