# e-comm template

A working e-commerce stack with no products in it. Accounts, cart, checkout,
card payments, receipt emails, analytics and an admin console are all built and
tested. What's missing is a business, so the shelves are stocked with
placeholders you'll delete.

Everything runs on your machine with one command. Everything in it is free and
open source except Stripe, which only charges you when you actually take money.

---

## Opening the site

The stack is running right now. On this machine the usual ports were already
taken by other software, so it's on shifted ports:

| | Address |
|---|---|
| Storefront | **http://localhost:3300** |
| Admin console | **http://localhost:3300/admin** |
| API | http://localhost:8088 |
| Interactive API docs | http://localhost:8088/docs |
| Mail catcher | http://localhost:8025 |
| Postgres | `localhost:5433` |

Open http://localhost:3300 and click around. The shop has four placeholder
products (one is a draft, so it correctly doesn't show up). Add something to
the cart, change quantities, go to checkout. Checkout will stop at the payment
step until you add Stripe keys, which is covered further down.

For the admin console, sign in with the email and password from your `.env`:

```bash
grep BOOTSTRAP_ADMIN .env
```

That gets you to the dashboard, order management, customer list, payments and
traffic analytics. Change that password once you're in.

If nothing loads, the containers may have been stopped:

```bash
make up       # start everything
make ps       # see what's running
make health   # ask every service if it's OK
```

One caveat about ports: `make up` prints `localhost:3000` and `localhost:8080`
because those are the defaults in `.env.example`. Your `.env` overrides them to
3300 and 8088. That's deliberate, don't "fix" it. If you move this to another
machine where 3000 and 8080 are free, the defaults will just work.

---

## Starting over from nothing

If you clone this somewhere else, or you blow away the data and want to start
again:

```bash
make setup   # copies .env.example to .env, generates every secret
make up      # builds ten containers and starts them
make seed    # loads the placeholder products
```

`make setup` generates strong random values for the database passwords, the JWT
signing key, the CSRF key and your admin password. It won't overwrite anything
you've already filled in, so it's safe to run again. The one thing it can't
generate is your Stripe keys.

`make clean` stops everything and deletes the database. There's a confirmation
prompt because it's not recoverable.

---

## Turning on payments

Checkout is fully wired but needs real Stripe test keys. Grab them from
[dashboard.stripe.com/test/apikeys](https://dashboard.stripe.com/test/apikeys)
and put them in `.env`:

```
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_test_...
```

Then you need Stripe to be able to reach your laptop, which it can't do
directly. Their CLI tunnels it:

```bash
stripe listen --forward-to localhost:8088/api/payments/webhook
```

That prints a `whsec_...` secret. Put it in `STRIPE_WEBHOOK_SECRET`, then
rebuild the frontend and restart payments:

```bash
make rebuild SVC=web
make restart SVC=payments
```

The frontend needs a rebuild rather than a restart because `NEXT_PUBLIC_*`
values get baked into the JavaScript bundle when it's built, not read at
runtime.

Until the webhook secret is set, orders will sit in `pending_payment` forever.
That's on purpose. Unsigned webhooks are rejected, because a public endpoint
that marks orders paid without checking the signature is a way to get free
merchandise. There's more on this in [docs/SECURITY.md](docs/SECURITY.md).

Test cards: `4242 4242 4242 4242` succeeds, `4000 0000 0000 0002` gets
declined. Any future expiry and any CVC.

---

## What it does now

Beyond accounts, cart, checkout and payments, the shop has:

- **Product management** in the admin console. Create and edit products,
  variants, prices and stock; upload, reorder and caption images.
- **Image hosting** built in. Files go to a volume, get content-addressed names
  and cache headers, and are stripped of EXIF and resized on upload. No S3
  account needed to start.
- **Five-star reviews**, gated on a verified purchase so ratings mean something.
- **Discount codes**, percent or fixed, with minimums, usage caps and windows.
- **Shipment tracking** — mark an order shipped with a carrier and number and
  the customer gets an email with a working tracking link.
- **SEO** — sitemap, structured data, and social preview cards, all behind one
  switch so nothing gets indexed before launch.

Still deliberately absent, with reasoning in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): gift cards, wishlists, loyalty
points, subscriptions, multi-currency, abandoned-cart email, a CMS, and
per-jurisdiction tax. That last one is the only real blocker to selling at
scale, and the fix is Stripe Tax rather than code.

---

## Why it's built this way

Most of these choices come down to the same three things: it's free, it's fast,
and it's boring enough that you can hire someone who already knows it.

### Postgres

Every order, price and account lives in Postgres.

The alternatives and why they lost:

**MySQL/MariaDB** would work fine. Postgres wins on the specific features this
app uses a lot: `CITEXT` gives case-insensitive email columns so `Alice@x.com`
and `alice@x.com` can't both register; `JSONB` stores shipping addresses
without inventing a column for every country's address format; `INET` stores IP
addresses properly; and partial and expression indexes are better. Also
`CHECK` constraints in MySQL were only enforced from 8.0.16, and this schema
leans on them hard.

**MongoDB** is the wrong shape for this. An order touches a user, several
product variants, a payment and an inventory count, and all of those need to
move together or not at all. That's a transaction across four entities, which
is exactly what relational databases are for and what document stores make you
hand-roll.

**SQLite** is tempting because it's free and needs no server, and it's genuinely
excellent. It falls down on concurrent writes. Two people checking out at the
same time will serialise, and one gets a locked-database error. Fine for a
prototype, not for a shop.

**DynamoDB, Firestore, and friends** cost money per request, lock you to one
cloud, and don't do joins. You'd be paying a monthly bill to make your own life
harder.

Postgres is free, runs on a $5 VPS, and every managed host offers it cheaply.
It'll handle more traffic than this shop is likely to see for years.

### One database, one schema per service

Textbook microservices give each service its own database. This gives each
service its own *schema* inside one database, plus its own login role.

The property that actually matters is that services can't read each other's
tables, and a role grant enforces that just as well. You can check it yourself:

```bash
make psql
```

```sql
-- connect as svc_orders and try to peek at accounts
SELECT count(*) FROM auth.users;
-- ERROR: permission denied for schema auth
```

What you give up is independent failure and independent scaling of storage.
That isn't worth six more database containers, six connection pools and six
backup jobs when you have no customers yet. When one service does outgrow the
box, moving it out is a `pg_dump` of one schema and a changed hostname, because
nothing in the code assumes they share a database. They talk over HTTP.

The honest reason: six Postgres instances cost six times as much to host.

### Python and FastAPI

You asked for Python. Within Python, FastAPI was the pick because:

- Validation comes free via Pydantic. Every request body is checked against a
  model before your code sees it, and `extra="forbid"` means somebody trying to
  smuggle `{"role": "admin"}` into a signup gets a 422 instead of a surprise.
- The OpenAPI docs at `/docs` are generated from the code, so they can't go
  stale.
- It's async, so a service waiting on Stripe isn't blocking other requests.

**Django** would have given you an admin panel for free, which is a real
argument. It's heavier, its ORM is synchronous by default, and the built-in
admin is hard to make look like anything. You wanted a specific aesthetic, so
that free admin would have been thrown away anyway.

**Flask** is lighter but you end up bolting on validation, async and schema
generation yourself.

### uv instead of pip or Poetry

uv resolves and installs the whole dependency tree in about a second. Poetry
takes closer to a minute on a project this size. It also does workspaces, so
all seven services share one lock file and two services can't end up on
incompatible versions of Pydantic.

It's free, it's a single static binary, and Docker builds are noticeably faster
because of it.

### Next.js

**Next.js** renders pages on the server, which matters once you have real
products, because a single-page app serves search engines an empty div. It also
has the most mature Stripe integration examples, deploys free on Vercel if you
ever want that, and is the default thing a frontend developer will already
know.

**Plain React with Vite** would be simpler and faster to build but has no
server rendering.

**HTMX with Python templates** would have kept the whole stack in one language
and would genuinely have been less code. It's a less conventional path for a
polished storefront, and you'd be fighting it the first time you want a live
cart badge or a Stripe card form.

### Tailwind

Tailwind v4 needs no config file at all; the theme is defined in CSS. The
palette and the Roman/vaporwave ornaments live in one file,
`web/src/app/globals.css`, and nothing else in the project needs to know about
them.

The alternative is writing CSS by hand, which is fine, but you end up inventing
a naming scheme and a spacing scale that Tailwind already has.

### No component library, no chart library, no icon font

The buttons, panels, tables, bar charts and funnel charts are all hand-written.
So are the icons.

Adding Material UI or Chart.js would mean shipping a few hundred kilobytes to
every visitor so that we can draw rectangles, and then fighting the library's
default look to get a vaporwave aesthetic out of it. The charts here are divs
and inline SVG. They're maybe 150 lines total, they theme themselves from the
same CSS variables as everything else, and they add nothing to the bundle.

Product images use a plain `<img>` rather than Next's `<Image>`, because
`<Image>` requires every image host to be listed in `next.config.ts` in advance,
and you'll be pasting in arbitrary URLs from the admin console.

### Stripe

Stripe takes a percentage of what you sell and charges nothing monthly, so it
costs you nothing until it's making you money.

The important thing is what it lets you *not* build. Card details go from the
customer's browser straight to Stripe. This codebase never sees a card number,
and there's no database column that could hold one. That keeps you out of
PCI-DSS scope entirely. Storing card numbers, even encrypted, drags you into a
compliance regime with annual audits and network segmentation requirements, and
there is no good reason to go there.

PayPal or Square would work similarly. Stripe has the best developer
documentation and the best test mode.

### dbt and DuckDB for analytics

This is the part worth explaining, because you originally asked for dbt to
handle migrations and it doesn't do that.

dbt builds *derived* tables from tables that already exist. It can't add a
column to your `orders` table without dropping and recreating it, which is fine
for a report and catastrophic for live data. Schema changes are Alembic's job.
Every model dbt manages here is disposable and gets rebuilt from scratch on
every run.

So what does it actually buy you?

- Your business definitions live in version control. "Revenue" is defined once,
  in `stg_orders.sql`, as orders that reached paid, fulfilled or delivered. The
  finance number and the product number can't disagree because they read the
  same definition.
- Tests run against the data itself. `make dbt-build` fails if revenue is ever
  negative, if a conversion rate exceeds 100%, or if an order line points at an
  order that doesn't exist. Those are the bugs that leave a dashboard rendering
  perfectly while showing a wrong number.
- `make dbt-docs` draws a lineage graph so you can see which dashboard breaks
  if you change a column.

It's free and it's the standard tool, so anyone you hire to do analytics will
already know it.

DuckDB is the engine underneath. Postgres is built for lots of small concurrent
writes; DuckDB is built for scanning millions of rows and grouping them. Running
a year-long report on the production database competes for the same connections
and memory that checkout needs. DuckDB reads Postgres directly over a read-only
attachment, so there's no copy step and no staleness, and the heavy scans happen
somewhere else entirely.

DuckDB is free, embedded in the process, and needs no server. A cloud data
warehouse like Snowflake or BigQuery does the same job with a monthly bill
attached, and you don't have the data volume to justify one.

### Docker Compose

One `docker-compose.yml` starts ten containers. No Python, Node or Postgres
installed on your machine.

Kubernetes does this properly at scale, and is enormous overkill here. Compose
files map almost directly onto ECS, Cloud Run and Container Apps when you
outgrow one machine. Until then a single VM runs the whole thing for a few
dollars a month.

### Mailpit for local email

Mailpit catches every outgoing email and shows it in a web UI at
http://localhost:8025. Nothing is actually sent. You can test the whole signup
and receipt flow without accidentally emailing a real person, and without
signing up for a mail provider before you need one. Free.

### Argon2id for passwords

Argon2id won the Password Hashing Competition and is what OWASP recommends
first. Unlike bcrypt it resists both GPU attacks and side-channel attacks, and
unlike PBKDF2 it's memory-hard, so purpose-built cracking hardware gains much
less.

Plain SHA-256 or MD5 for passwords would be negligent. They're designed to be
fast, which is exactly wrong.

### Sessions in cookies rather than localStorage

The session token lives in an `httpOnly` cookie, which JavaScript cannot read.
Putting it in `localStorage` is more convenient, and it means one compromised
npm package or one XSS bug leaks every visitor's session.

The cost of cookies is CSRF exposure, which is a much easier problem to solve
completely, and is solved here twice over (`SameSite=Lax` plus a double-submit
token).

### GitHub Actions

Free for public repositories. Runs the linter, the type checker, the tests and
a full Docker build of all eight images on every push.

That last one matters more than it sounds. A service can import a library it
never declared as a dependency, and it'll work perfectly on your machine
because some other service pulled that library in. It only breaks when the
image is built in isolation. That's precisely how a missing `email-validator`
dependency got caught here.

---

## Every file in the repo

### Top level

| File | What it does |
|---|---|
| `README.md` | This. |
| `Makefile` | Every command you'll run. `make` on its own lists them. It's mostly thin wrappers over `docker compose` so you don't have to remember flags. |
| `docker-compose.yml` | Defines all ten containers, the network between them, the two data volumes, health checks and log rotation. The YAML anchors at the top (`x-service-build`, `x-common-env`) exist so a change to the build recipe happens once instead of six times. |
| `pyproject.toml` | The uv workspace root. Lists the members, and holds the Ruff, pytest and mypy config so every service is linted identically. |
| `uv.lock` | Exact pinned versions for the whole Python dependency tree. Committed on purpose, so builds are reproducible. |
| `.python-version` | Pins Python 3.12 so uv doesn't resolve against whatever you have installed. |
| `.env.example` | Every environment variable with a comment explaining it. Copy to `.env`; `.env` is gitignored and never committed. |
| `.gitignore` | Keeps secrets, build output, virtualenvs and `node_modules` out of the repo. |
| `.dockerignore` | Keeps the same junk out of Docker build contexts, which makes builds much faster. |

### `docker/`

| File | What it does |
|---|---|
| `python-service.Dockerfile` | One Dockerfile that builds all seven Python services; a build arg picks which. Multi-stage, so the shipped image has no build tools in it. Dependencies are installed in a separate layer from your source, so editing a file rebuilds in seconds rather than re-downloading everything. Runs as a non-root user. |
| `entrypoint.sh` | Runs when a Python container starts. Waits for Postgres to actually accept queries, applies that service's migrations, then starts the web server. Running migrations here means the schema can never be older than the code querying it. |
| `dbt.Dockerfile` | Builds the dbt container. Separate from the services because dbt drags in a large dependency tree that has no business in something serving HTTP traffic. |
| `postgres/010-init-roles.sh` | Runs once, the first time the database is created. Creates a login role and a schema per service, grants each one access to its own schema and nothing else, and revokes the permissive defaults Postgres ships with. This is the file that makes the service isolation real. |

### `scripts/`

| File | What it does |
|---|---|
| `generate_secrets.py` | Backs `make secrets`. Replaces every `CHANGE_ME` placeholder in `.env` with a strong random value, sized and encoded appropriately for what it is. Skips Stripe keys, since a fake one would produce a confusing crash instead of a clear "you forgot this". Sets the file to owner-only permissions. |
| `health_check.sh` | Backs `make health`. Curls every service's `/health` and prints a table. Exits non-zero if anything is unhealthy, so it doubles as a CI smoke test. |
| `scaffold_alembic.py` | Generates a service's migration setup from the shared templates. Means a fix to the migration environment is made once and regenerated, rather than copy-pasted six times. |
| `templates/alembic.ini.tmpl` | Template for a service's Alembic config. Deliberately contains no database URL, so no credential is ever committed. |
| `templates/alembic_env.py.tmpl` | Template for the migration environment. The comments in here are worth reading: it explains why the search path excludes the service's own schema (including it makes Alembic propose dropping and recreating every table) and why `alembic_version` is excluded from autogenerate. |
| `templates/script.py.mako` | The template new migration files are generated from. Includes a checklist about table-locking operations. |

### `packages/ecom-shared/`

The library every service is built on. Cross-cutting concerns live here so they
behave identically everywhere, because inconsistency between services is where
security holes come from.

| File | What it does |
|---|---|
| `pyproject.toml` | Package manifest. |
| `src/ecom_shared/__init__.py` | Re-exports the public API and has a table explaining what each module is for. |
| `config.py` | Base settings class. Reads environment variables and validates them at startup, so a missing signing key crashes the container immediately instead of surfacing days later as a mysterious 500. |
| `logging.py` | Structured JSON logging. Every line carries a request ID, so one customer action can be traced across all seven services by filtering on one value. |
| `errors.py` | The error classes and the handlers that turn every failure into one JSON shape. Unhandled exceptions become a generic 500 with the traceback going only to the logs, because exception text leaks table names and file paths. |
| `middleware.py` | Assigns request IDs, writes one access log line per request, and attaches browser security headers. |
| `db.py` | Async SQLAlchemy engine and session, pinned to one service's schema. The session commits when a handler returns and rolls back if it raises, so a request either fully happened or didn't. |
| `security.py` | Password hashing, token generation and JWT signing. Small on purpose; every function delegates to a vetted library and the only decisions made are parameter choices, each with a comment explaining it. |
| `identity.py` | Works out who's calling and whether they're allowed. Explains why each service verifies the token itself instead of trusting a header from the gateway. |
| `schemas.py` | Base Pydantic model and the pagination envelope. Sets up camelCase on the wire and snake_case in Python so no TypeScript has to translate. |
| `app.py` | `create_service_app()`. Wires up everything above plus health probes. This is why every service's `main.py` is about ten lines. |
| `tests/test_security.py` | Tests for the crypto helpers, including a check that a forged `alg: none` JWT is rejected. |

### `services/gateway/`

The only thing the browser talks to. Owns no database.

| File | What it does |
|---|---|
| `pyproject.toml` | Dependencies. |
| `src/ecom_gateway/config.py` | Settings, including the route table. It's a fixed map of six names to six URLs, so a crafted path can't make the gateway connect to an arbitrary host. |
| `src/ecom_gateway/proxy.py` | The reverse proxy. Streams request and response bodies rather than buffering them, which matters because Stripe's webhook signature covers the literal bytes. Refuses to route any path containing `/internal/`. |
| `src/ecom_gateway/csrf.py` | Double-submit CSRF checking, with a comment listing what's exempt and why each exemption is safe. |
| `src/ecom_gateway/rate_limit.py` | Per-IP request counting. Honest about its limits in the docstring: it's in-process, so two gateway replicas would double the effective limit. |
| `src/ecom_gateway/main.py` | Wires it together and owns the shared HTTP client, so connections to the backend services are pooled. |

### `services/auth/`

Accounts, sessions and roles. The most security-sensitive service.

| File | What it does |
|---|---|
| `src/ecom_auth/config.py` | Settings, including the admin email allowlist and lockout thresholds. |
| `src/ecom_auth/models.py` | Five tables: users, refresh tokens, password reset tokens, login attempts and audit events. No plaintext secret is stored anywhere. |
| `src/ecom_auth/schemas.py` | Request and response shapes. The password rules live here so signup, password change and reset can't drift apart. |
| `src/ecom_auth/service.py` | The actual rules. Registration, login with timing equalisation, refresh token rotation with theft detection, password changes and admin updates. Read this file to understand the service. |
| `src/ecom_auth/cookies.py` | Sets and clears session cookies, with the reasoning for every flag. |
| `src/ecom_auth/router.py` | Public routes: register, login, refresh, logout, profile, sessions, password reset. |
| `src/ecom_auth/admin_router.py` | Admin routes: list users, change status or role, read the audit log. Protected at the router level, so a new endpoint is protected by default. |
| `src/ecom_auth/bootstrap.py` | Creates your admin account on first boot, solving the chicken-and-egg problem of a fresh install with nobody who can sign in. Idempotent and does nothing if not configured. |
| `src/ecom_auth/deps.py` | FastAPI dependencies, which is what lets tests swap the database for a transaction that rolls back. |
| `src/ecom_auth/main.py` | Entry point. |
| `tests/conftest.py` | Test fixtures. Explains why tests run against real Postgres and why each one is wrapped in a rolled-back transaction. |
| `tests/test_auth_flows.py` | The security test suite. Each test says what an attacker would gain if it failed: token rotation, reuse detection, lockout, account enumeration, privilege escalation, SQL injection. |
| `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako` | Migration config, generated from the shared templates. |
| `migrations/versions/*_initial_auth_schema.py` | The migration that creates the tables. |

### `services/catalog/`

Products, variants, categories and stock. Prices live here and nowhere else.

| File | What it does |
|---|---|
| `src/ecom_catalog/config.py` | Settings. |
| `src/ecom_catalog/models.py` | Category → product → variant. The docstring explains why product and variant are separate tables even if you only ever sell one size of everything. |
| `src/ecom_catalog/schemas.py` | Request and response shapes. Note that the public variant response exposes `inStock` but not the exact stock count, since that's competitive information. |
| `src/ecom_catalog/service.py` | Queries and stock movement. The interesting part is `reserve_inventory`, which prevents two shoppers buying the last item with a single conditional SQL UPDATE rather than a read-then-write in Python. |
| `src/ecom_catalog/router.py` | Public storefront routes. Only returns active products, and the status filter isn't a parameter, because a public endpoint that accepts `?status=draft` is a preview of everything you haven't launched. |
| `src/ecom_catalog/admin_router.py` | Admin CRUD and catalogue statistics. |
| `src/ecom_catalog/internal_router.py` | Service-to-service routes. This is where the authoritative prices come from when orders needs to price a cart. |
| `src/ecom_catalog/seed.py` | `make seed`. Loads the four placeholder products. Delete this once you have real ones. |
| `src/ecom_catalog/deps.py`, `main.py` | Dependencies and entry point. |
| `alembic.ini`, `migrations/` | Migrations. |

### `services/orders/`

Carts, checkout and the order lifecycle. The most complicated business logic.

| File | What it does |
|---|---|
| `src/ecom_orders/config.py` | Settings including tax rate, shipping rules and the URLs of the services it calls. |
| `src/ecom_orders/models.py` | Carts, cart items, orders, order items and order events. Explains why order lines store a copy of the product title and price rather than a reference, and includes the legal state transitions as data. |
| `src/ecom_orders/schemas.py` | Request and response shapes. The checkout request deliberately contains no monetary amount at all. |
| `src/ecom_orders/service.py` | Cart handling, live repricing, the checkout saga with its compensating rollback, and the order state machine. |
| `src/ecom_orders/clients.py` | HTTP clients for catalog, payments and notifications. Every outbound call has a timeout and turns failures into one error type. |
| `src/ecom_orders/router.py` | Customer routes: cart, checkout, order history. |
| `src/ecom_orders/admin_router.py` | Admin routes: all orders, status changes, revenue statistics. |
| `src/ecom_orders/internal_router.py` | Called by payments when Stripe confirms or declines a payment. Nothing reachable from a browser can mark an order paid. |
| `src/ecom_orders/deps.py`, `main.py` | Dependencies and entry point. `main.py` has a comment about router mount order, because getting it wrong made the admin order list unreachable. |
| `alembic.ini`, `migrations/` | Migrations. |

### `services/payments/`

Stripe. Holds no card data.

| File | What it does |
|---|---|
| `src/ecom_payments/config.py` | Stripe keys, stored as secret types so they can't be printed by accident. |
| `src/ecom_payments/models.py` | Payments, refunds and received webhook events. The docstring is mostly about what's deliberately absent. |
| `src/ecom_payments/schemas.py` | Request and response shapes. |
| `src/ecom_payments/stripe_gateway.py` | The only file that imports the Stripe SDK. Isolating it means the rest of the service is testable without network access, and swapping payment providers is one file. Signature verification lives here. |
| `src/ecom_payments/service.py` | Payment intent creation with double idempotency, webhook handling, and refunds. |
| `src/ecom_payments/router.py` | The public webhook endpoint, the internal intent endpoint, and admin refund routes. |
| `src/ecom_payments/deps.py`, `main.py` | Dependencies and entry point. |
| `alembic.ini`, `migrations/` | Migrations. |

### `services/analytics/`

Traffic events and the admin dashboards.

| File | What it does |
|---|---|
| `src/ecom_analytics/config.py` | Settings including the salt rotation period and retention window. |
| `src/ecom_analytics/models.py` | The events table and daily rollups. The docstring lists what's deliberately not stored: no raw IPs, no full user agents, no query strings. |
| `src/ecom_analytics/service.py` | Event recording with hashing and classification, plus the dashboard queries. |
| `src/ecom_analytics/duckdb_reader.py` | Reads the marts dbt builds. Degrades to returning nothing if dbt hasn't run yet, so a missing analytics file can't take the admin console down. |
| `src/ecom_analytics/router.py` | The public event ingest endpoint and the admin dashboard endpoints. |
| `src/ecom_analytics/main.py` | Entry point. |
| `alembic.ini`, `migrations/` | Migrations. |

### `services/notifications/`

Transactional email.

| File | What it does |
|---|---|
| `src/ecom_notifications/config.py` | SMTP settings and retry limits. |
| `src/ecom_notifications/models.py` | The outbox table. The docstring explains why sending email inside a request is a bad idea. |
| `src/ecom_notifications/templates_registry.py` | The email templates, in Python rather than files so a missing template is an import error at startup instead of a runtime failure during checkout. Every template renders both HTML and plain text. |
| `src/ecom_notifications/service.py` | Queueing, delivery and retry with exponential backoff. Workers claim rows with `FOR UPDATE SKIP LOCKED`, so running two of them doesn't send anything twice. |
| `src/ecom_notifications/worker.py` | The background loop. Every iteration is wrapped in its own error handling, because an unhandled exception would end the task silently and the service would look healthy while never sending another email. |
| `src/ecom_notifications/router.py` | The internal send endpoint and admin views of what's been sent. |
| `src/ecom_notifications/main.py` | Entry point. |
| `alembic.ini`, `migrations/` | Migrations. |

### `analytics/dbt_ecom/`

| File | What it does |
|---|---|
| `dbt_project.yml` | Project config. The header explains the Alembic/dbt split. |
| `profiles.yml` | Connection config. DuckDB as the engine with Postgres attached read-only. No credentials in the file, so it's safe to commit. |
| `models/sources.yml` | Declares the Postgres tables dbt reads, with tests on them. Also sets freshness thresholds, which is how you find out that ingest has silently stopped rather than assuming it's been a quiet week. |
| `models/staging/stg_orders.sql` | Orders, cleaned. Defines `is_revenue` once for the whole project. |
| `models/staging/stg_order_items.sql` | Purchased lines. |
| `models/staging/stg_users.sql` | Accounts. Deliberately never selects the password hash. |
| `models/staging/stg_product_variants.sql` | Variants joined to products, with stock flags. |
| `models/staging/stg_payments.sql` | Stripe payment attempts. |
| `models/staging/stg_events.sql` | Traffic events. |
| `models/staging/staging.yml` | Documentation and tests for the staging layer. |
| `models/marts/mart_daily_revenue.sql` | Daily orders and revenue, built on a generated date spine so quiet days show as zero instead of vanishing from the chart. |
| `models/marts/mart_product_performance.sql` | What actually sells, joined to current stock so a best seller running low is visible on the same row. |
| `models/marts/mart_customer_value.sql` | Lifetime value per customer, keyed on email so guest orders and later account orders belong to the same person. |
| `models/marts/mart_traffic_daily.sql` | Daily traffic and conversion rate. |
| `models/marts/mart_funnel.sql` | The checkout funnel, counted on distinct sessions rather than events. |
| `models/marts/marts.yml` | Documentation and the 72 data tests. |
| `macros/test_accepted_range.sql` | A custom test asserting a column stays within bounds. Written locally rather than pulled from `dbt_utils` to avoid a network fetch and a package directory. |
| `macros/cents_to_currency.sql` | Formats integer cents for display. The docstring warns never to use it inside an aggregation. |
| `.gitignore` | Excludes dbt's build output. |

### `web/`

| File | What it does |
|---|---|
| `package.json` | Dependencies. Deliberately short: Next, React, Stripe's two packages, and nothing else at runtime. |
| `package-lock.json` | Exact pinned versions. |
| `tsconfig.json` | TypeScript config. Strict mode plus `noUncheckedIndexedAccess`, which turns `items[0]` into a possibly-undefined value and catches the empty-array crash that otherwise only shows up in production. |
| `next.config.ts` | Standalone output for small Docker images, plus browser security headers. |
| `postcss.config.mjs` | Hooks Tailwind in. Three lines. |
| `eslint.config.mjs` | Linting. |
| `Dockerfile` | Three-stage build producing a ~150MB image instead of ~1GB. |
| `.dockerignore` | Keeps `node_modules` out of the build context. |
| `public/robots.txt` | Blocks search engines. This is a placeholder shop; you don't want "Placeholder Item 01" indexed. Delete before launch. |

#### `web/src/lib/`

| File | What it does |
|---|---|
| `api.ts` | The only way the app talks to the API. Handles which base URL to use, the CSRF header, and silent token refresh on a 401. The refresh is deduplicated through a single promise, because five parallel refreshes would look like token theft and sign the user out of everything. |
| `types.ts` | TypeScript mirrors of the API's response shapes. |
| `format.ts` | Money, number and date formatting. Money is divided by 100 here and nowhere else. |
| `analytics.ts` | Event reporting. Uses `sendBeacon` so events fired during a page transition aren't cancelled. Failures are swallowed, because nobody can buy anything from an error boundary. |
| `use-async.ts` | Shared data-fetching hook. Handles loading state, errors and the out-of-order response race where a slow first request lands after a fast second one. |

#### `web/src/components/`

| File | What it does |
|---|---|
| `ui.tsx` | Buttons, panels, headings, pills, empty states, skeletons and the Greek fret divider. Server components, so none of them ship JavaScript. |
| `session-provider.tsx` | Session and cart state. They're in one provider because they're coupled: signing in claims your guest cart. |
| `site-header.tsx` | Navigation, cart badge, account links, mobile menu. |
| `site-footer.tsx` | Footer. |
| `product-card.tsx` | Product tile, including the drawn placeholder artwork for products with no image. |
| `add-to-cart.tsx` | Variant picker and add button. The only interactive part of a product page. |
| `auth-shell.tsx` | Shared frame for sign in, sign up and password reset, so the three can't drift apart. |
| `admin-ui.tsx` | Stat cards, tables, bar chart and funnel chart. The charts include a plain-text `<details>` equivalent so they're not invisible to a screen reader. |
| `page-view-tracker.tsx` | Reports a page view on each client-side navigation, which a normal analytics snippet would miss entirely in an app router. |

#### `web/src/app/` — storefront

| File | What it does |
|---|---|
| `layout.tsx` | Root layout. Loads the fonts, sets metadata, mounts the providers, and includes a skip-to-content link for keyboard users. |
| `globals.css` | The whole design system. Palette, typography, the Greek fret, the fluted column texture, the horizon grid, the sun, the scanlines. Everything visual starts here. |
| `page.tsx` | Home page and hero. |
| `shop/page.tsx` | Product listing with category filter, search and pagination. State lives in the URL, so every view is shareable and the back button works. |
| `shop/[slug]/page.tsx` | Product detail. |
| `cart/page.tsx` | The cart. |
| `checkout/page.tsx` | Address collection, then Stripe Elements. |
| `checkout/success/page.tsx` | Post-payment confirmation. Doesn't mark anything paid, because arriving at a URL proves nothing. |
| `login/page.tsx` | Sign in. Only honours same-origin redirects, closing the open-redirect that `?next=` invites. |
| `register/page.tsx` | Sign up. |
| `forgot-password/page.tsx` | Request a reset link. |
| `reset-password/page.tsx` | Redeem one. |
| `account/page.tsx` | Profile and order history. |
| `account/orders/[id]/page.tsx` | A single order as the customer sees it. |
| `about/page.tsx` | Placeholder about page that honestly says this is a template. |
| `privacy/page.tsx` | Privacy policy describing what the software actually does. Needs a lawyer before launch. |
| `terms/page.tsx` | Empty on purpose. Boilerplate terms would be wrong for your business. |
| `not-found.tsx` | 404. |
| `error.tsx` | Error boundary. Shows an error reference rather than the message, since exception text can leak internals. |
| `api/healthz/route.ts` | Health endpoint for the container check. |

#### `web/src/app/admin/` — admin console

| File | What it does |
|---|---|
| `layout.tsx` | The admin shell and navigation. The guard here is convenience only; every admin endpoint checks the role server-side, and the file says so. |
| `page.tsx` | Dashboard. Revenue, traffic, funnel, catalogue and account health. Each panel fetches and fails independently, so one service being down greys out its own card instead of blanking the page. |
| `orders/page.tsx` | Order list with filtering and search. |
| `orders/[id]/page.tsx` | One order, with buttons to advance its status. Only offers transitions that are actually legal. |
| `products/page.tsx` | Catalogue overview and stock warnings. |
| `customers/page.tsx` | Customer accounts, with enable and disable. |
| `payments/page.tsx` | Payments and refunds. Refunds use a browser confirm, on purpose; this moves real money and shouldn't be a one-click mistake. |
| `traffic/page.tsx` | Traffic analytics with a window selector. |

### `.github/workflows/`

| File | What it does |
|---|---|
| `ci.yml` | Lint, type check and test Python against a real Postgres; lint, type check and build the frontend; build all eight Docker images. |
| `dbt.yml` | Parses every dbt model so a broken reference fails in review rather than at 3am. |

### `docs/`

| File | What it does |
|---|---|
| `ARCHITECTURE.md` | Every significant decision and its trade-off, ending with a table of what's deliberately simplified and when to revisit it. |
| `SECURITY.md` | What each control prevents, including where a trade-off was made against usability. |
| `DEPLOYMENT.md` | What changes when this faces the internet. Read it before going live. |

---

## Day-to-day commands

Run `make` for the full list.

```bash
make up                  # build and start everything
make down                # stop, keep the data
make logs                # tail everything
make logs SVC=orders     # tail one service
make health              # check every service
make ps                  # container status

make rebuild SVC=orders  # rebuild one service after a code change
make psql                # open a database shell
make shell SVC=orders    # open a shell in a container

make test                # Python tests
make lint                # linters
make typecheck           # type checkers
make check               # everything CI runs

make migration SVC=orders M="add gift message"   # generate a migration
make migrate SVC=orders                          # apply migrations

make dbt-build           # rebuild the analytics models and test them
make dbt-docs            # lineage graph on :8081

make seed                # placeholder products
make clean               # stop and delete all data
```

---

## Changing things

### Adding a field to a table

```bash
# edit services/orders/src/ecom_orders/models.py
make migration SVC=orders M="add gift message"
# read the generated file in services/orders/migrations/versions/
make rebuild SVC=orders
```

Always read the generated migration. Autogenerate is good but it isn't
infallible, particularly around renames, which it sees as a drop plus an add.

### Changing the look

Start in `web/src/app/globals.css`. The palette is at the top as CSS variables,
and the ornaments are near the bottom. Changing `--color-neon` recolours the
whole site.

### Adding a page

Drop a `page.tsx` into a new folder under `web/src/app/`. That's the whole
routing system.

### Adding an endpoint

Add the route to the relevant service's `router.py` and the logic to
`service.py`. If it's for admins, put it in `admin_router.py` and it inherits
the role check. If it's for another service rather than a browser, put it in
`internal_router.py` and the gateway won't expose it.

### Frontend development with hot reload

```bash
make web-dev
```

Runs Next on your machine against the containerised API, so changes appear
instantly instead of needing a rebuild.

---

## Testing

```bash
make test    # 48 tests
make check   # linters, type checkers and tests, same as CI
```

Python integration tests run against real Postgres inside a transaction that
gets rolled back, rather than against SQLite or mocks. The schema leans on
`CITEXT`, `INET`, `JSONB` and constraint behaviour that a stand-in wouldn't
reproduce, and a test suite that passes against a different database isn't
testing what you ship.

---

## Before going live

Don't put this on the internet without reading
[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and the checklist at the end of
[docs/SECURITY.md](docs/SECURITY.md). The short version:

- `ENVIRONMENT=production` and `COOKIE_SECURE=true`
- HTTPS, with the site and the API under one domain
- Stop publishing ports 8001–8006; only the gateway should be reachable
- Fresh secrets, and change the bootstrap admin password
- Delete `web/public/robots.txt` and the `robots` metadata in `layout.tsx`
- Set up backups, then test restoring one

## Licence

None chosen yet. Pick one before you publish or distribute this.
