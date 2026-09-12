# Architecture

Why the system is shaped the way it is. Each section states a decision, what it
buys, and what it costs — the trade-offs are the useful part.

---

## Microservices, but one database

Seven services, one Postgres instance, one schema per service.

Textbook microservices give each service its own *database*. The property that
actually matters is that no service can reach into another's tables, and a
Postgres schema plus a per-service role enforces that just as well: the
`svc_orders` role cannot `SELECT` from `auth.users`, because the grant does not
exist. This is verifiable, not aspirational:

```sql
-- as svc_orders
SELECT count(*) FROM auth.users;
-- ERROR: permission denied for schema auth
```

**What you give up** is independent failure and independent scaling of the
storage layer. That is not worth six more containers, six connection pools and
six backup jobs at this size.

**When one service outgrows it**, moving out is a `pg_dump` of a single schema
and a changed `POSTGRES_HOST`. Nothing in the code assumes co-location: services
talk to each other over HTTP, never through shared tables.

See `docker/postgres/010-init-roles.sh` for the roles and grants.

---

## Every service verifies its own tokens

The gateway does not tell a service who the caller is. It forwards the token,
and each service verifies the signature itself.

The tempting alternative — gateway validates once, passes `X-User-Id: <uuid>`
downstream — is faster and is exactly how internal services get compromised. The
moment anything other than the gateway can open a connection to the orders
service (a misconfigured port, a compromised sidecar, a developer with
`kubectl port-forward`) that header becomes a "log in as anyone" button.

Verifying at every hop costs roughly 20 microseconds.

---

## Prices come from the catalogue, never the client

The checkout request contains an email, an address, and nothing monetary. Not a
total, not a unit price, not a discount.

Orders asks catalog what the cart costs, over an internal endpoint. A client
that can name its own price can buy anything for a penny, and accepting a
client-supplied total is the most commonly exploited e-commerce flaw there is.

The request models use Pydantic's `extra="forbid"`, so a smuggled
`"unitPriceCents": 1` is a 422 rather than a silently ignored field.

---

## Order lines are snapshots, not references

An `OrderItem` stores the SKU, product title, variant name and unit price *as
they were at purchase*. It does not join to the catalogue to render them.

- Prices change; an invoice must show what was actually paid.
- Products get renamed, archived, and occasionally deleted by mistake.
- You generally must be able to reproduce a historical invoice for years.

`variant_id` is kept for analytics and reordering, but nothing about displaying
an order depends on it still resolving.

---

## Checkout is a saga, with compensation

Payment involves Stripe, which knows nothing about our database transaction, so
checkout cannot be one atomic unit. It is a sequence with a compensating action:

1. Reprice the cart from the catalogue.
2. **Reserve stock** — before taking money. Reserving afterwards means a
   customer can be charged for something you cannot ship.
3. Create the order as `pending_payment`.
4. Create the Stripe payment intent.
5. Return the client secret to the browser.

If step 4 fails, the reservation is released and the order is cancelled. That
compensation is committed explicitly, because the exception being re-raised
would otherwise roll it back and leave stock reserved against an order nobody
will ever pay for.

Verified end to end: with Stripe unconfigured, a checkout attempt returns 502,
stock returns to its original level, and the order lands in `cancelled` with the
reason recorded in its history.

---

## Only a verified webhook can mark an order paid

The browser arriving at `/checkout/success` proves only that the browser arrived
there. The URL is trivially forged.

An order becomes `paid` when Stripe's HMAC-signed webhook says so — verified in
the payments service, then relayed to orders over an internal endpoint the
gateway refuses to proxy. The confirmation page therefore shows the order's
*real* status and is honest that confirmation is asynchronous.

Webhook handling is idempotent (Stripe retries for up to three days) and
verifies that the captured amount matches the order total.

---

## Inventory races are prevented in SQL, not in Python

The naive reservation reads the quantity, checks it, and writes back. Two
checkouts for the last unit both read `1`, both conclude there is enough, and
one item is sold twice.

Instead each line is a single conditional `UPDATE`:

```sql
UPDATE product_variants
   SET inventory_quantity = inventory_quantity - :qty
 WHERE id = :id AND inventory_quantity >= :qty
```

Postgres evaluates the condition and applies the decrement atomically under a
row lock. The second attempt matches zero rows, which the service detects and
rejects. A `CHECK (inventory_quantity >= 0)` constraint sits beneath that as a
backstop.

---

## Email goes through an outbox

A send request writes a row and returns; a background worker does the SMTP
conversation with exponential backoff.

Sending inline fails in exactly the situation that matters most: if the mail
server is down, checkout *fails*, and a customer who has already been charged
sees an error. Decoupling means a mail outage delays receipts and nothing else.

Workers claim rows with `FOR UPDATE SKIP LOCKED`, so running a second replica
sends nothing twice.

---

## DuckDB for analysis, Postgres for transactions

Postgres is right for concurrent writes, row locks and foreign keys. It is not
right for "scan every event of the last year and group it four ways" — and
running that on the production database competes for the buffer pool and
connections that checkout needs.

dbt uses DuckDB as its engine with Postgres **attached read-only**. There is no
ETL step and no staleness: it reads live transactional tables, aggregates with a
columnar engine, and writes marts to a file the analytics service reads.

The `dbt_runner` role holds `SELECT` and nothing else, with `CREATE` nowhere. A
broken model cannot corrupt an order.

**Alembic owns the application schema; dbt owns the layer above it.** dbt models
are disposable and fully rebuilt on each run. Pointing dbt at your `orders`
table means the first `--full-refresh` drops it.

---

## Money is always an integer

`total_cents: int`, never `total: float`. Binary floats cannot represent 0.10
exactly, so float money accumulates rounding error until a customer is charged a
cent more than the page showed. Stripe's API works in minor units for the same
reason. Division by 100 happens once, at display time.

A database `CHECK` enforces `total_cents = subtotal_cents + tax_cents +
shipping_cents`, so a bug in the checkout calculation fails loudly on insert
rather than quietly charging the wrong amount.

---

## Things deliberately left simple

Honest about what this is not:

| Simplification | When to revisit |
|----------------|-----------------|
| Rate limiting is in-process | The moment you run two gateway replicas. Move the counter to Redis; the interface is one small file. |
| The outbox worker runs inside the notifications service | When mail volume competes with request handling. It is already a standalone loop — change the entrypoint. |
| Tax is a flat basis-point rate | Before selling across jurisdictions. Use Stripe Tax or a dedicated service. |
| No search engine | When `ILIKE` stops being fast enough or you need relevance ranking. |
| Sessions are stateless JWTs with a 15-minute window | If you need instant global revocation rather than 15-minute-bounded. |
| Admin product editing is read-only in the UI | The API supports full CRUD; the form is left to build once the catalogue's real shape is known. |
