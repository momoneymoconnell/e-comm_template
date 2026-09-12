-- Stripe payment attempts.
--
-- `amount_received_cents` is what Stripe confirms it actually captured, which
-- is the figure finance cares about. `amount_cents` is only what we asked for;
-- the two differing is a signal worth investigating, not a rounding artefact.

with source as (
    select * from {{ source('payments', 'payments') }}
),

renamed as (
    select
        id                          as payment_id,
        order_id,
        order_number,
        payment_intent_id,
        status,
        stripe_status,

        amount_cents                as requested_cents,
        amount_received_cents       as captured_cents,
        lower(currency)             as currency,

        status = 'succeeded'        as is_captured,
        failure_code,

        cast(created_at   as timestamp) as created_at,
        cast(succeeded_at as timestamp) as succeeded_at

    from source
)

select * from renamed
