-- What actually sells.
--
-- Counted from order_items, which are purchase-time snapshots, so a renamed or
-- archived product still appears under the name it was sold as. Joining to the
-- live catalogue would make historical figures shift whenever a title changed.
--
-- Only revenue-bearing orders are counted: an abandoned cart is not a sale.

with items as (
    select * from {{ ref('stg_order_items') }}
),

orders as (
    select * from {{ ref('stg_orders') }} where is_revenue
),

variants as (
    select * from {{ ref('stg_product_variants') }}
),

sold as (
    select
        i.sku,
        i.product_title,
        i.product_slug,
        i.variant_name,
        i.variant_id,

        sum(i.quantity)                    as units_sold,
        sum(i.line_total_cents)            as revenue_cents,
        count(distinct i.order_id)         as orders,
        cast(avg(i.unit_price_cents) as bigint) as avg_sale_price_cents,
        min(o.order_date)                  as first_sold_on,
        max(o.order_date)                  as last_sold_on

    from items i
    inner join orders o on o.order_id = i.order_id
    group by i.sku, i.product_title, i.product_slug, i.variant_name, i.variant_id
)

select
    s.sku,
    s.product_title,
    s.product_slug,
    s.variant_name,
    s.units_sold,
    s.revenue_cents,
    s.orders,
    s.avg_sale_price_cents,
    s.first_sold_on,
    s.last_sold_on,

    -- Current catalogue state, so the dashboard can flag a best seller that is
    -- about to go out of stock -- the single most actionable thing on the page.
    v.current_price_cents,
    v.inventory_quantity,
    v.is_out_of_stock,
    v.is_low_stock,
    v.product_status

from sold s
left join variants v on v.variant_id = s.variant_id
order by s.revenue_cents desc
