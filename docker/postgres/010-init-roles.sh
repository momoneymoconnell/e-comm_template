#!/usr/bin/env bash
# =============================================================================
# Runs ONCE, the first time the Postgres data volume is created.
#
# Creates one login role and one schema per service, and grants each role
# access to its own schema and nothing else. This is what makes the "each
# service owns its data" boundary real rather than a naming convention: if the
# orders service is compromised, `SELECT * FROM auth.users` fails with a
# permission error, because that grant was never issued.
#
# To re-run after changing this file you must destroy the volume:
#     docker compose down -v && docker compose up -d
# `docker compose restart` will NOT re-run it.
# =============================================================================
set -euo pipefail

echo "[init] creating per-service roles, schemas and grants"

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v db_name="${POSTGRES_DB}" \
     -v auth_pw="${SVC_AUTH_DB_PASSWORD}" \
     -v catalog_pw="${SVC_CATALOG_DB_PASSWORD}" \
     -v orders_pw="${SVC_ORDERS_DB_PASSWORD}" \
     -v payments_pw="${SVC_PAYMENTS_DB_PASSWORD}" \
     -v analytics_pw="${SVC_ANALYTICS_DB_PASSWORD}" \
     -v notifications_pw="${SVC_NOTIFICATIONS_DB_PASSWORD}" \
     -v dbt_pw="${DBT_DB_PASSWORD}" <<'SQL'

-- ---------------------------------------------------------------------------
-- Lock down the defaults first.
--
-- Postgres ships with every role able to create objects in `public` and every
-- role able to connect to any database. Both are historical defaults that make
-- isolation impossible, so they are revoked before anything else is created.
-- ---------------------------------------------------------------------------
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON DATABASE :"db_name" FROM PUBLIC;

-- ---------------------------------------------------------------------------
-- Extensions.
-- pgcrypto provides gen_random_uuid(), used for every primary key.
-- citext gives a case-insensitive text type so Alice@x.com and alice@x.com
-- collide on the unique index — which is what users expect of an email address.
-- Both live in `public` so all services can reach the types.
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS citext   WITH SCHEMA public;
GRANT USAGE ON SCHEMA public TO PUBLIC;

-- ---------------------------------------------------------------------------
-- Service roles.
--
-- NOINHERIT means a role gets exactly the privileges granted to it directly,
-- never anything picked up through membership in another role. It removes a
-- whole class of "how does this account have that permission?" surprises.
-- ---------------------------------------------------------------------------
CREATE ROLE svc_auth          LOGIN NOINHERIT PASSWORD :'auth_pw';
CREATE ROLE svc_catalog       LOGIN NOINHERIT PASSWORD :'catalog_pw';
CREATE ROLE svc_orders        LOGIN NOINHERIT PASSWORD :'orders_pw';
CREATE ROLE svc_payments      LOGIN NOINHERIT PASSWORD :'payments_pw';
CREATE ROLE svc_analytics     LOGIN NOINHERIT PASSWORD :'analytics_pw';
CREATE ROLE svc_notifications LOGIN NOINHERIT PASSWORD :'notifications_pw';

-- dbt only ever reads the transactional schemas and writes to its own two.
CREATE ROLE dbt_runner LOGIN NOINHERIT PASSWORD :'dbt_pw';

-- ---------------------------------------------------------------------------
-- Schemas. AUTHORIZATION makes the role the owner, which is what lets its own
-- Alembic migrations run DDL there without any further grant.
-- ---------------------------------------------------------------------------
CREATE SCHEMA auth          AUTHORIZATION svc_auth;
CREATE SCHEMA catalog       AUTHORIZATION svc_catalog;
CREATE SCHEMA orders        AUTHORIZATION svc_orders;
CREATE SCHEMA payments      AUTHORIZATION svc_payments;
CREATE SCHEMA analytics     AUTHORIZATION svc_analytics;
CREATE SCHEMA notifications AUTHORIZATION svc_notifications;

-- dbt's own output schemas: raw-ish staging models, then the marts the admin
-- dashboard reads.
CREATE SCHEMA analytics_stg   AUTHORIZATION dbt_runner;
CREATE SCHEMA analytics_marts AUTHORIZATION dbt_runner;

-- ---------------------------------------------------------------------------
-- Connect privilege. Everyone needs it; nobody had it after the REVOKE above.
-- ---------------------------------------------------------------------------
GRANT CONNECT ON DATABASE :"db_name" TO
    svc_auth, svc_catalog, svc_orders, svc_payments,
    svc_analytics, svc_notifications, dbt_runner;

-- ---------------------------------------------------------------------------
-- dbt: read-only on every transactional schema.
--
-- The ALTER DEFAULT PRIVILEGES line is the important half. A plain GRANT
-- applies only to tables that exist right now, so the first table added by a
-- future migration would be invisible to dbt and the model would break with a
-- permission error. Default privileges apply to everything created later, by
-- that owner, forever.
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA auth, catalog, orders, payments, analytics TO dbt_runner;

GRANT SELECT ON ALL TABLES IN SCHEMA auth      TO dbt_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA catalog   TO dbt_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA orders    TO dbt_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA payments  TO dbt_runner;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO dbt_runner;

ALTER DEFAULT PRIVILEGES FOR ROLE svc_auth          IN SCHEMA auth          GRANT SELECT ON TABLES TO dbt_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE svc_catalog       IN SCHEMA catalog       GRANT SELECT ON TABLES TO dbt_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE svc_orders        IN SCHEMA orders        GRANT SELECT ON TABLES TO dbt_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE svc_payments      IN SCHEMA payments      GRANT SELECT ON TABLES TO dbt_runner;
ALTER DEFAULT PRIVILEGES FOR ROLE svc_analytics     IN SCHEMA analytics     GRANT SELECT ON TABLES TO dbt_runner;

-- ---------------------------------------------------------------------------
-- The analytics service reads the marts dbt produces, so the admin dashboard
-- can serve aggregates without ever touching a transactional table.
-- ---------------------------------------------------------------------------
GRANT USAGE ON SCHEMA analytics_marts TO svc_analytics;
GRANT SELECT ON ALL TABLES IN SCHEMA analytics_marts TO svc_analytics;
ALTER DEFAULT PRIVILEGES FOR ROLE dbt_runner IN SCHEMA analytics_marts
    GRANT SELECT ON TABLES TO svc_analytics;

-- ---------------------------------------------------------------------------
-- Deliberate non-grants, stated explicitly so the intent is auditable:
--
--   * No service can read another service's schema. Cross-service data is
--     fetched over HTTP, where it is authorised and logged.
--   * dbt_runner has SELECT only. It cannot INSERT, UPDATE or DELETE anything
--     in a transactional schema, so a broken model can never corrupt an order.
--   * No role has SUPERUSER or CREATEDB.
-- ---------------------------------------------------------------------------
SQL

echo "[init] roles, schemas and grants created"
