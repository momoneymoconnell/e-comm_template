# e-comm template

A deployable full-stack e-commerce skeleton. Python microservices behind a
gateway, Postgres for transactions, dbt + DuckDB for analytics, and a Next.js
storefront with an admin console.

**The catalogue is intentionally empty.** Every system works — accounts, cart,
checkout, payments, email, analytics — but no business has been decided, so the
shelves hold placeholders. This is the machinery, ready for a business to be
dropped into it.

---

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) with Compose v2. Nothing
else — Python, Node and Postgres all run inside containers.

```bash
make setup          # create .env and generate every secret
make up             # build and start all ten containers
make seed           # load placeholder products (optional)
```

Then open:

| What | Where |
|------|-------|
| Storefront | http://localhost:3000 |
| Admin console | http://localhost:3000/admin |
| API gateway | http://localhost:8080 |
| API docs | http://localhost:8080/docs |
| Mail catcher | http://localhost:8025 |

Sign in to the admin console with the `BOOTSTRAP_ADMIN_EMAIL` and
`BOOTSTRAP_ADMIN_PASSWORD` in your `.env`. **Change that password, then clear it
from `.env`.**

> **Ports already in use?** `make up` fails if something owns 3000, 8080 or
> 5433. Set `WEB_HOST_PORT`, `GATEWAY_HOST_PORT` or `POSTGRES_HOST_PORT` in
> `.env` and update `NEXT_PUBLIC_API_URL`, `PUBLIC_WEB_URL` and
> `CORS_ALLOW_ORIGINS` to match, then `make up` again.

### To take payments

Checkout needs Stripe test keys. Get them from
[dashboard.stripe.com/test/apikeys](https://dashboard.stripe.com/test/apikeys)
and put them in `.env`:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_test_...
```

Then forward webhooks to your machine, so payments actually confirm:

```bash
stripe listen --forward-to localhost:8080/api/payments/webhook
```

Copy the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`, then
`make rebuild SVC=web && make restart SVC=payments`.

Until the webhook secret is set, orders stay in `pending_payment`: unsigned
webhooks are rejected, which is deliberate. See
[docs/SECURITY.md](docs/SECURITY.md).

---

## What is running

```
browser ──► web (Next.js :3000)
              │
              └──► gateway (:8080) ──┬──► auth          (:8001)
                                     ├──► catalog       (:8002)
                                     ├──► orders        (:8003)
                                     ├──► payments      (:8004)
                                     ├──► analytics     (:8005)
                                     └──► notifications (:8006)
                                             │
                                     postgres (:5433)  ◄── dbt (on demand)
```

| Service | Owns | Responsibility |
|---------|------|----------------|
| **gateway** | — | The only public entrypoint. Routing, rate limiting, CSRF. |
| **auth** | `auth` schema | Accounts, sessions, roles, audit trail. |
| **catalog** | `catalog` schema | Products, variants, categories, inventory. **Prices live here.** |
| **orders** | `orders` schema | Carts, checkout, order lifecycle. |
| **payments** | `payments` schema | Stripe intents, webhooks, refunds. No card data. |
| **analytics** | `analytics` schema | Traffic events and admin dashboards. |
| **notifications** | `notifications` schema | Transactional email via a durable outbox. |

Plus Postgres, [Mailpit](http://localhost:8025) (catches all outbound mail in
development), and a dbt container that runs on demand.

Architecture decisions and their reasoning:
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Everyday commands

Run `make` on its own for the full list.

```bash
make up                  # build and start everything
make down                # stop (data is kept)
make logs                # tail all logs
make logs SVC=orders     # tail one service
make health              # check every service's health endpoint
make ps                  # container status

make rebuild SVC=orders  # rebuild and restart one service after a code change
make psql                # open psql as the database owner
make shell SVC=orders    # a shell inside a container

make test                # Python test suite
make lint                # ruff + eslint
make typecheck           # mypy + tsc
make check               # everything CI runs

make migration SVC=orders M="add gift message"   # autogenerate a migration
make migrate SVC=orders                          # apply migrations

make dbt-build           # build and test the analytics models
make dbt-docs            # lineage docs on :8081

make clean               # stop and DELETE all data
```

---

## Making changes

### Backend

Each service is a small FastAPI app in `services/<name>/src/ecom_<name>/`, laid
out the same way:

| File | Contains |
|------|----------|
| `config.py` | Settings, read from the environment and validated at startup |
| `models.py` | Database tables |
| `schemas.py` | Request and response shapes |
| `service.py` | Business logic — **the file to read first** |
| `router.py` | HTTP routes. Thin: they translate HTTP and call `service.py` |
| `admin_router.py` | Admin-only routes |
| `internal_router.py` | Service-to-service routes, unreachable from a browser |
| `main.py` | ~10 lines of wiring |

Everything structural — logging, error shapes, security headers, health probes,
database lifecycle — comes from `packages/ecom-shared`, so it is written once
and cannot drift between services.

To change a table:

```bash
# 1. Edit services/orders/src/ecom_orders/models.py
make migration SVC=orders M="add gift message"   # writes a migration file
# 2. READ the generated file. Autogenerate is a good assistant, not an oracle.
make rebuild SVC=orders                          # applies it on startup
```

### Frontend

`web/` is a standard Next.js App Router project. `src/lib/api.ts` is the single
way it talks to the API — it handles the CSRF header, silent token refresh and
the shared error shape, so no component deals with any of that.

```bash
make web-dev     # dev server with hot reload on :3000
```

Note that `NEXT_PUBLIC_*` values are baked into the bundle at **build** time, so
changing one needs `make rebuild SVC=web`, not a restart.

### Analytics

`analytics/dbt_ecom/` is a dbt project. Staging views normalise the source
tables; marts aggregate them. `make dbt-build` runs the models and their tests.

**dbt does not manage the application schema** — Alembic does. dbt reads those
tables and builds derived models from them. Pointing dbt at your `orders` table
would mean the first `--full-refresh` drops it.

---

## Testing

```bash
make test        # 48 tests
make check       # lint + typecheck + test, as CI runs it
```

Python integration tests run against real Postgres inside a transaction that is
rolled back, rather than against SQLite or mocks — the schema leans on CITEXT,
INET, JSONB and constraint behaviour that a substitute would not reproduce.

The auth suite is written as security assertions: token rotation, reuse
detection, brute-force lockout, account-enumeration resistance, privilege
escalation and SQL injection each have a test that states what an attacker
would gain if it failed.

---

## Going to production

This runs locally as-is. Before it faces the internet, work through
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — it covers the settings that are
deliberately relaxed for local development (`COOKIE_SECURE`, `ENVIRONMENT`,
published ports, CORS) and what changes when you move to a managed database.

The security model, and the specific attacks each control prevents, is in
[docs/SECURITY.md](docs/SECURITY.md).

---

## Layout

```
.
├── packages/ecom-shared/     shared library every service builds on
├── services/
│   ├── gateway/              public entrypoint
│   ├── auth/                 accounts and sessions
│   ├── catalog/              products and inventory
│   ├── orders/               carts and checkout
│   ├── payments/             Stripe
│   ├── analytics/            traffic events
│   └── notifications/        email outbox
├── analytics/dbt_ecom/       dbt models and tests
├── web/                      Next.js storefront and admin
├── docker/                   Dockerfiles and Postgres init
├── docs/                     architecture, security, deployment
├── scripts/                  secret generation, health check, scaffolding
└── docker-compose.yml
```

## Licence

No licence has been chosen. Add one before publishing or distributing this.
