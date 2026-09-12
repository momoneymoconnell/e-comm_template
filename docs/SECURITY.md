# Security

What is protected, how, and what each control actually prevents. Where a
decision is a trade-off, the trade-off is stated.

---

## Passwords

Argon2id, the current OWASP first choice: unlike bcrypt it resists GPU *and*
side-channel attacks, and unlike PBKDF2 it is memory-hard, so custom cracking
hardware gains far less.

Parameters follow the OWASP baseline — 19 MiB, 2 iterations, 1 degree of
parallelism. Hashes carry their own parameters, and a successful login
transparently upgrades a hash made with older ones, so cost can be raised over
time without a forced reset.

Minimum length 12, following NIST SP 800-63B: length beats composition rules,
which mostly teach people to write `Summer2024!`. A blocklist rejects common
passwords including padded and repeated variants (`password123`,
`passwordpassword`). Maximum length 128 is a denial-of-service guard — Argon2
cost grows with input, so an unbounded field lets someone post a 10 MB
"password" and burn a CPU core per request.

**Add a [Have I Been Pwned](https://haveibeenpwned.com/API/v3#PwnedPasswords)
range check before launch.** The built-in blocklist is a starting point.

---

## Sessions

Two tokens, both in `httpOnly` cookies:

| | Lifetime | Revocable | Purpose |
|---|---|---|---|
| Access | 15 min | No | Sent on every request |
| Refresh | 30 days | **Yes** | Exchanged for a new pair |

Access tokens are self-contained JWTs and cannot be withdrawn early, which is
precisely why they are short-lived. The refresh token is the part with staying
power, so it is the part kept under control: stored as a SHA-256 hash, so a
database dump yields no working sessions.

### Rotation with reuse detection

Every refresh consumes the old token and issues a new one, linked in a chain. If
an already-rotated token is presented again, exactly one of two things happened:
the legitimate client replayed a request, or an attacker is using a stolen copy.
We cannot tell which, so we assume the worst and **revoke the entire chain**.

A stolen refresh token is therefore worth at most one request, instead of
indefinite silent access.

> This is also why the frontend deduplicates concurrent refreshes through a
> single in-flight promise. Five parallel refreshes would look exactly like
> theft and sign the user out of everything.

### Why cookies and not `localStorage`

`localStorage` is readable by every script on the page. One compromised
dependency or one reflected XSS and every visitor's token is exfiltrated. An
`httpOnly` cookie is invisible to JavaScript, so the same bug yields nothing
persistent.

The cost is CSRF exposure, which is a much easier problem to solve completely.

---

## CSRF

Two independent layers:

1. **`SameSite=Lax`** on session cookies. The browser does not attach them to
   cross-site POSTs at all, which kills the classic hidden-form attack outright.
2. **Double-submit tokens.** State-changing requests must echo the `csrf_token`
   cookie in an `X-CSRF-Token` header. An attacker's page can cause the cookie
   to be *sent* but cannot *read* it, so it cannot populate the header.

The first layer is enforced by the browser, the second by us. Browser defaults
have shifted more than once, and the header check costs a few lines.

Exempt, deliberately:

| Path | Why it is safe |
|------|----------------|
| `/api/payments/webhook` | Server-to-server, no cookies. Authenticated by HMAC signature instead — a stronger check. |
| `login`, `register`, `refresh`, password reset | No session exists to forge. Requiring a token first is a chicken-and-egg problem. |
| `/api/analytics/events` | Writes only pseudonymous counters. A forged one skews a statistic. |
| Any `Authorization: Bearer` request | Not sent automatically by the browser, so not forgeable cross-site. |

**Deployment note.** `SameSite=Lax` treats `example.com` and `api.example.com`
as same-site, but not `example.com` and `myapp.vercel.app`. Serve the API and
the frontend under one apex domain, or sessions will not work.

---

## Stripe and PCI scope

**No card data is stored, processed or transmitted by any service here.** There
is no column for a PAN, expiry or CVC, and there should never be.

Card details go from the browser directly to Stripe via Stripe Elements. Our
servers only ever see an opaque payment-intent ID. That single decision keeps
the system out of PCI-DSS scope — storing a card number, even encrypted, pulls
you into a regime with annual audits and network segmentation requirements.

### Webhook verification is the boundary

The webhook endpoint is public by necessity. Without signature verification,
anyone could POST `{"type": "payment_intent.succeeded"}` and have orders marked
paid and goods dispatched for free.

Every event is HMAC-verified against the endpoint secret before any business
logic runs, using the **raw request body** — parsing and re-serialising the JSON
first changes key order and whitespace and breaks verification.

The service refuses to process unsigned events. With `STRIPE_WEBHOOK_SECRET`
unset, payments never confirm. That is the correct failure mode.

Events are deduplicated on a unique constraint (Stripe retries for three days),
and the captured amount is compared against the order total — a mismatch is
rejected, not fulfilled.

---

## Authorisation

Role is embedded in the access token, so services authorise without a round trip
to auth on every request. The trade-off is up to 15 minutes of staleness after a
demotion, bounded by the token TTL — and any role or status change immediately
revokes every session for that user, which closes the window when it matters.

Admin routes are guarded at the **router** level, so a new endpoint is protected
by default rather than by remembering to add a decorator.

### Promotion requires a deployment-level allowlist

Granting `admin` requires the target's address to appear in `ADMIN_EMAILS`,
which lives in the environment. A compromised admin session alone therefore
cannot mint a second, persistent admin — that needs access to the deployment.

Admins also cannot change their own role or disable themselves, which prevents
both accidental self-lockout and quiet entrenchment.

### The frontend guard is not a control

`web/src/app/admin/layout.tsx` hides the admin UI from non-admins. It is
convenience. Every admin endpoint verifies the role server-side; a customer
calling the API with curl gets 403 regardless of what the browser renders.

---

## Account enumeration

An unauthenticated endpoint that reveals which emails are registered is a
customer-list extraction tool.

| Endpoint | Behaviour |
|----------|-----------|
| Password reset | **Identical** response and timing whether or not the account exists. Email dispatch failures are swallowed so they cannot reintroduce a timing difference. |
| Login | One message for wrong password, unknown user and disabled account. A full Argon2 verification runs against a dummy hash for unknown users, so the ~50 ms difference is not an oracle. |
| Registration | **Returns 409 for a duplicate.** A deliberate exception — see below. |

Registration is the one place UX wins. The enumeration-proof alternative is to
return success and email the existing owner, which leaves a real customer at a
dead end mid-checkout. The sensitive path — password reset — is fully protected.

---

## Brute force

More than 10 failed attempts for one address within 15 minutes rejects further
attempts. Keyed on **email, not IP**: an attacker with a botnet has unlimited
addresses but only one target account.

Attempts are recorded in their own transaction, committed *before* the 401 is
raised. This matters more than it sounds: an earlier version wrote the row
inside the request transaction, where the exception rolled it back — the counter
never incremented and the lockout, though fully implemented, did nothing at all.
The same bug silently disabled refresh-token reuse detection. Both are now
covered by tests.

The gateway additionally rate-limits every client to 120 requests per minute.

---

## Database isolation

Each service logs in as its own role, granted access to its own schema and
nothing else. `PUBLIC` privileges on the database and on `public` are revoked
before anything is created.

```sql
-- as svc_orders
SELECT count(*) FROM auth.users;
-- ERROR: permission denied for schema auth
```

`dbt_runner` holds `SELECT` only, everywhere, with `CREATE` nowhere.

---

## Privacy

Analytics tables are where e-commerce systems most often accumulate personal
data nobody decided to collect, and then keep forever.

| Not stored | Stored instead |
|------------|----------------|
| IP address | Salted hash, salt rotates daily |
| Full user agent | `"mobile"` / `"chrome"` |
| Query strings | Path only |
| Full referrer URL | Host only |

Rotating the salt daily is the important part: a fixed salt is a permanent
pseudonymous identifier, which GDPR treats as personal data. Rotation caps how
long anyone can be followed while still answering "how many distinct people came
today". Any figure summed across days is therefore **visitor-days**, and the
dashboard says so rather than overstating it.

`DNT: 1` is honoured — the request is accepted and nothing is recorded.

Raw events expire after ~13 months; the aggregates dbt builds survive.
Transactional email is purged after 90 days.

---

## Errors and logging

One JSON error envelope everywhere. Unhandled exceptions become a generic 500
with the traceback going to the logs only — exception text leaks table names,
file paths and library versions to anyone probing.

Every response carries an `X-Request-ID` that appears on every log line across
every service, so a user can quote it and you can reconstruct the whole path.

Logs deliberately exclude passwords, tokens, API keys and card data. The `User`
model's `__repr__` omits the email address, because reprs end up in tracebacks
and an email address is personal data.

The audit log records security-relevant actions — role changes, deactivations,
password resets, and **admin reads of customer records**. It is written in the
same transaction as the action it describes, so it never records something that
was rolled back.

---

## Before you go live

- [ ] `ENVIRONMENT=production` — disables `/docs` and error detail in responses
- [ ] `COOKIE_SECURE=true` — required for the `__Host-` cookie prefix
- [ ] Serve everything over HTTPS under one apex domain
- [ ] Rotate every secret; `make secrets` values are for local use
- [ ] Change the bootstrap admin password and clear it from `.env`
- [ ] Stop publishing service ports 8001–8006; only the gateway should be reachable
- [ ] Set `CORS_ALLOW_ORIGINS` to your real origin
- [ ] Add a Have I Been Pwned check to registration
- [ ] Add email verification before first purchase
- [ ] Move rate limiting to Redis if running more than one gateway
- [ ] Set up backups, and **test a restore**
- [ ] Configure real SMTP with SPF, DKIM and DMARC
- [ ] Review `docs/DEPLOYMENT.md`

## Reporting

This is a template, not a deployed service. If you find a flaw in it, open an
issue. If you find one in *your* deployment of it, treat it as your own
incident.
