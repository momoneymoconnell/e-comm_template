-- Accounts.
--
-- Note what is not selected: password_hash. dbt's role can read it, and no
-- model here ever should. An analytics warehouse is queried by more people,
-- from more places, with looser access control than the application database
-- -- so credential material must not reach it, even hashed.

with source as (
    select * from {{ source('auth', 'users') }}
),

renamed as (
    select
        id                              as user_id,
        lower(email)                    as email,
        -- The domain is useful (how many customers are on corporate mail?)
        -- without carrying the full address into every downstream model.
        split_part(lower(email), '@', 2) as email_domain,
        role,
        is_active,
        email_verified_at is not null   as is_email_verified,

        cast(created_at    as timestamp) as created_at,
        cast(last_login_at as timestamp) as last_login_at,
        cast(created_at    as date)      as signup_date

    from source
)

select * from renamed
