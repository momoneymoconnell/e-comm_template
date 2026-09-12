-- Purchasable variants joined to their product, for readable reporting.

with variants as (
    select * from {{ source('catalog', 'product_variants') }}
),

products as (
    select * from {{ source('catalog', 'products') }}
),

joined as (
    select
        v.id                    as variant_id,
        v.sku,
        v.name                  as variant_name,
        v.price_cents           as current_price_cents,
        lower(v.currency)       as currency,
        v.inventory_quantity,
        v.track_inventory,
        v.is_active             as variant_is_active,

        p.id                    as product_id,
        p.slug                  as product_slug,
        p.title                 as product_title,
        p.status                as product_status,
        p.category_id,

        -- Flags the dashboard's "needs attention" list is built from.
        v.track_inventory and v.inventory_quantity = 0   as is_out_of_stock,
        v.track_inventory and v.inventory_quantity <= 5  as is_low_stock,

        cast(v.created_at as timestamp) as created_at

    from variants v
    left join products p on p.id = v.product_id
)

select * from joined
