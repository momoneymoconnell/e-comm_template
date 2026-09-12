# Deployment

The stack runs locally as-is. This is what changes when it faces the internet.

Nothing here is cloud-specific until the last section — the same compose file
runs on a single VM, and every managed-service variant is a changed environment
variable.

---

## What is deliberately relaxed locally

These settings make local development work over plain HTTP. **All of them are
wrong in production.**

| Setting | Local | Production | Why |
|---------|-------|------------|-----|
| `ENVIRONMENT` | `development` | `production` | Gates `/docs`, error detail in responses, and permissive CORS. Also makes weak secrets a hard startup failure rather than a warning. |
| `COOKIE_SECURE` | `false` | `true` | A `Secure` cookie is silently dropped over HTTP — locally that means login appears to work and every later request is anonymous. Also required for the `__Host-` prefix. |
| `COOKIE_DOMAIN` | `localhost` | your apex domain | |
| `CORS_ALLOW_ORIGINS` | `http://localhost:3000` | your real origin | |
| Ports 8001–8006 | published | **not published** | Only the gateway should be reachable. |
| Postgres port | published on 127.0.0.1 | **not published** | |
| `BOOTSTRAP_ADMIN_PASSWORD` | in `.env` | removed after first login | |

---

## Before the first deploy

```bash
# 1. Fresh secrets. Never reuse the local ones.
make secrets

# 2. Live Stripe keys and a webhook endpoint pointed at
#    https://yourdomain.com/api/payments/webhook

# 3. Confirm production settings
grep -E '^(ENVIRONMENT|COOKIE_SECURE|CORS_ALLOW_ORIGINS)=' .env
```

Then work through the checklist at the end of
[SECURITY.md](SECURITY.md#before-you-go-live).

---

## TLS and one apex domain

Put a reverse proxy (Caddy, nginx, or a cloud load balancer) in front of the
gateway and terminate TLS there.

**Serve the frontend and the API under one apex domain**:

```
https://example.com        → web
https://example.com/api    → gateway
```

or `example.com` and `api.example.com`. What must *not* happen is the frontend
on one registrable domain and the API on another — `SameSite=Lax` cookies are
not sent cross-site, and sessions simply will not work. This surfaces as "login
returns 200 but I am still signed out", which is a miserable afternoon.

If the proxy sits in front of the gateway, configure it to **overwrite**
`X-Forwarded-For` rather than append. Otherwise a client can spoof its IP and
defeat rate limiting.

---

## Production compose

`docker-compose.yml` runs in production with three changes. Put them in an
override file rather than editing the original:

```yaml
# docker-compose.prod.yml
services:
  auth:          { ports: !reset [] }
  catalog:       { ports: !reset [] }
  orders:        { ports: !reset [] }
  payments:      { ports: !reset [] }
  analytics:     { ports: !reset [] }
  notifications: { ports: !reset [] }
  postgres:      { ports: !reset [] }

  gateway:
    ports: ["127.0.0.1:8080:8000"]   # behind the reverse proxy only
```

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

Set resource limits too — an unbounded container can starve its neighbours:

```yaml
    deploy:
      resources:
        limits: { cpus: "1.0", memory: 512M }
```

---

## Migrations

Each service applies its own Alembic migrations on startup, so the schema can
never be older than the code querying it. Alembic takes a Postgres advisory
lock, so starting several replicas at once is safe — one migrates, the others
wait and find nothing to do.

This is the right default. It does mean a bad migration blocks startup, which is
what you want.

**Two rules for migrations that touch live data:**

1. **Deploy code first, schema second** when removing anything. Dropping a
   column the running code still reads causes errors until the deploy finishes.
2. **Watch for table rewrites.** `ALTER TABLE ... ADD COLUMN` with a non-null
   default rewrites the whole table on older Postgres and holds a lock for the
   duration. Add the column nullable, backfill in batches, then set `NOT NULL`.

---

## Managed Postgres

Replace the `postgres` service with your provider and point the services at it:

```
POSTGRES_HOST=your-instance.rds.amazonaws.com
POSTGRES_PORT=5432
```

Then run `docker/postgres/010-init-roles.sh`'s SQL once by hand — it only runs
automatically on a fresh local volume. Everything in it (roles, schemas, grants,
`pgcrypto`, `citext`) is required.

Connection pooling: each service opens 5 connections with 10 overflow. Seven
services at two replicas each is up to 210 connections, above the common
default of 100. Either lower `pool_size` or put PgBouncer in front.

---

## Analytics in production

`make dbt-build` is a manual command. Schedule it — hourly is usually plenty,
since these are dashboards, not transactions:

```
0 * * * * cd /srv/ecom && docker compose run --rm dbt build
```

The DuckDB marts file lives on a Docker volume shared between the dbt container
and the analytics service. If you split them onto different hosts, put the file
on shared storage or move the marts into Postgres instead.

---

## Backups

The `postgres_data` volume is the only thing that cannot be rebuilt.

```bash
docker compose exec -T postgres pg_dump -U ecom -Fc ecom > backup-$(date +%F).dump
```

**Test a restore.** An untested backup is not a backup, and the moment you find
out is the worst possible moment.

The DuckDB marts are fully derived — rebuild with `make dbt-build` rather than
backing them up.

---

## Observability

Every service emits structured JSON logs with a request ID that propagates
across all of them, so any log aggregator can reconstruct a request path by
filtering on one value.

Endpoints per service:

| Endpoint | Meaning |
|----------|---------|
| `/health` | Full report including database reachability |
| `/health/live` | Process is alive. Checks nothing else on purpose — if liveness depended on Postgres, a brief blip would restart every healthy container at once. |
| `/health/ready` | Should receive traffic. 503 when the database is unreachable, so the load balancer routes around it *without* restarting it. |

Worth alerting on:

- `payment_amount_mismatch` — a charge that does not match its order
- `stripe_webhook_signature_invalid` — misconfiguration, or someone forging events
- `refresh_token_reuse_detected` — a stolen token, or a client bug
- `stock_release_failed` — stock reserved against an order that will not complete
- `notification_permanently_failed` — email given up on after 5 attempts

---

## Scaling, in the order it will matter

1. **Web and gateway** are stateless — add replicas freely. Move rate limiting
   to Redis at the same time, or the effective limit multiplies per replica.
2. **Services** are stateless too, but watch the connection count above.
3. **Postgres** vertically first, then read replicas for analytics — though dbt
   and DuckDB already keep the heavy queries off the transactional path.
4. **Split a schema out** when one genuinely outgrows the instance: `pg_dump`
   one schema, change that service's `POSTGRES_HOST`. Nothing in the code
   assumes co-location.

---

## Cloud notes

The stack is plain containers with no host dependencies, so it runs anywhere
that runs OCI images.

| Target | Notes |
|--------|-------|
| **Single VM** | Simplest. Compose plus Caddy for automatic TLS. Genuinely fine into meaningful traffic. |
| **ECS / Cloud Run / Container Apps** | One task per service. Use the managed secret store rather than `.env`. Cloud Run needs a minimum instance count, or the outbox worker is frozen between requests. |
| **Kubernetes** | One Deployment per service, `/health/live` and `/health/ready` map directly to probes. Only worth it if you already run Kubernetes. |

Whatever the target: secrets belong in the platform's secret manager, not in a
`.env` file baked into an image.
