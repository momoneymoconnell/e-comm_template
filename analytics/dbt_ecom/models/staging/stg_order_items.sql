-- Purchased lines.
--
-- These are snapshots: the product title and price are as they were at the
-- moment of purchase, not as they are now. Joining back to the catalogue to
-- "correct" them would silently rewrite history and make old revenue figures
-- shift every time a price changes.

with source as (
    select * from {{ source('orders', 'order_items') }}
),

renamed as (
    select
        id                        as order_item_id,
        order_id,
        variant_id,
        sku,
        product_title,
        product_slug,
        variant_name,

        unit_price_cents,
        quantity,
        total_cents                as line_total_cents,

        cast(created_at as timestamp) as created_at

    from source
)

select * from renamed
