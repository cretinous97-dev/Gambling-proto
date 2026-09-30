# Naktsang Casino

A complete, working online casino: real accounts, real balances, real double-entry
accounting, real deposits and withdrawals, 11 games, a player wallet, and an
operator back office.

**Status: fully functional on a laptop, running in sandbox payment mode.**
Money movements are genuinely transactional — the ledger, the holds, the admin
approvals and the audit trail are the real thing. What is *simulated* is the
connection to a payment provider, because moving real money requires **your**
licence and **your** merchant account. See [Your part to do](#your-part-to-do).

> **Read the next section before you point a domain at this.**
> Running a real-money gambling site without a licence is a criminal offence in
> most countries, and the technical work here is the easy half.

---

## Contents

- [What works](#what-works)
- [Quick start](#quick-start)
- [Your part to do](#your-part-to-do) ← **start here**
- [How the money works](#how-the-money-works)
- [The games](#the-games)
- [Provably fair](#provably-fair)
- [Compliance and player protection](#compliance-and-player-protection)
- [Deploying](#deploying)
- [Configuration](#configuration)
- [Banking methods](#banking-methods--adding-a-payment-rail-without-a-deploy)
- [Testing](#testing)
- [Project layout](#project-layout)
- [Known gaps](#known-gaps)

---

## What works

Everything below is verified by an automated end-to-end run against the live API
(`make smoke` — 50 checks, all passing), not by hand-waving.

| Area | Status |
|---|---|
| Registration, login, JWT + refresh, logout, password change | working |
| Double-entry ledger, integer-cent money, zero-sum enforced on every transaction | working |
| Deposits: create → provider → settle/chargeback, idempotent credit | working (sandbox provider) |
| Withdrawals: request → hold funds → admin review → payout or release | working |
| Admin back office: 9 screens incl. payout queue, players, ledger, audit, risk | working |
| 11 games with published RTPs | working |
| Provably fair (HMAC-SHA256 commit/reveal, player-verifiable) | working |
| Responsible gambling: deposit/loss limits, cool-off, self-exclusion | working |
| KYC submission + verification gate above a threshold | working |
| Live crash game over WebSockets | working |
| Bonus engine: first-deposit bonus, promo codes, wagering requirements | working |
| Rakeback, VIP tiers, jackpot pool, leaderboards | working |
| ~81 backend tests incl. exact RTP proofs, statistical RTP checks and deploy guards | passing |
| Runs on a serverless platform (no lifespan, no loops, no persistent disk) | working, see [Deploying](#deploying) |

---

## Quick start

You need Python 3.11+ and Node 18+.

```bash
make install          # creates .venv, installs backend + frontend deps
make api              # terminal 1 — API on http://localhost:8000
make web              # terminal 2 — site on http://localhost:5173
```

Open <http://localhost:5173>, register a player, and the whole flow is live.

**Seeded operator account** (change it immediately):

```
admin@casino.example.com  /  Admin!2345
```

To move money through the system with the sandbox provider:

1. Register a player and open **Wallet**.
2. Make a deposit — the sandbox provider returns `requires_action`, exactly like
   a real card 3-D Secure step.
3. Press **Simulate success** (only available in sandbox mode; the endpoint
   returns 403 the moment you switch to a real provider). The ledger is credited.
4. Play anything.
5. Request a withdrawal. The cash moves to a held account immediately.
6. Sign in as the admin, open **Operations → Withdrawals**, approve it.
7. Check **Operations → Ledger**: every transaction still sums to zero.

Verify the whole path from the command line:

```bash
make smoke            # deposit -> play every game -> withdraw -> payout -> books balance
```

---

## Your part to do

The software is built, tested and runnable. **These are the things only you can
do**, roughly in the order you should do them. Nothing in this list is optional
if real players and real money are involved.

> **Going live?** [`DEPLOY-CHECKLIST.md`](DEPLOY-CHECKLIST.md) is the actionable
> version: exactly which credentials are needed, where to put them, how to keep
> them out of a chat log, and the order to do things in.

### 1. Licensing and legal — do this first

- [ ] **Get a gambling licence** in the jurisdiction you intend to operate from
      (Malta MGA, Isle of Man, Curaçao, Anjouan, Kahnawake, your local regulator…).
      Everything else below depends on which one you get, because the licence
      dictates the rules you must implement.
- [ ] **Get legal advice in your target markets.** The code ships with a
      jurisdiction blocklist defaulting to `US,GB,FR,NL,AU`, but that list is a
      placeholder, not legal advice.
- [ ] **Write the real legal documents.** The API serves terms, privacy, AML,
      responsible-gambling and bonus-policy pages, but they are *templates* —
      every one contains an `OPERATOR MUST INSERT` marker where your company
      name, address, licence number and regulator go. They are not legal advice
      and must be reviewed by a gambling lawyer before launch.
- [ ] **Corporate structure, bank account, tax registration.** You cannot
      receive player funds into a personal account, and commingling player money
      with operating money is both a licensing breach and a way to lose
      everything if the company fails.
- [ ] **Player funds segregation.** The ledger already separates player balances
      from house revenue; legally you will still be required to hold player
      funds in a segregated account, and in some jurisdictions in a trust.

### 2. Payments — the part that actually moves money

The payment layer is a clean interface with three implementations: a sandbox
simulator (default), Stripe, and a crypto/on-chain provider. The live adapters
are written against each provider's real API shape but **have never been run
against a live account** — that is what your merchant onboarding is for.

- [ ] **Open a merchant account with a gambling-friendly PSP.** Ordinary Stripe
      accounts will not process gambling; you need their gambling programme or a
      specialist acquirer (Worldpay, Nuvei, Paysafe, Checkout.com, or a crypto
      processor).
- [ ] **Put your keys in `backend/.env`** and set `PAYMENT_PROVIDER=stripe` (or
      `cryptopay`).
- [ ] **Register the webhook endpoint** with the provider and put the signing
      secret in `STRIPE_WEBHOOK_SECRET` / `CRYPTOPAY_WEBHOOK_SECRET`. The app
      rejects unsigned webhooks; test this with the provider's CLI before launch.
- [ ] **Verify deposits are only credited after settlement**, and that
      chargebacks debit the player and flag the account — then decide your
      chargeback policy with your acquirer.
- [ ] **Decide the payout process.** `AUTO_APPROVE_WITHDRAWAL_UNDER_USD=0` means
      every withdrawal waits for a human. Keep it that way until you trust your
      fraud checks.
- [ ] **Crypto specifics, if you use them:** pick chains, set confirmation
      counts, fund a hot wallet for payouts, and keep the cold wallet offline.
      Never store payout private keys on the same machine as the web app.

### 3. Identity, fraud and AML

- [ ] **Choose a KYC provider** (Sumsub, Onfido, Veriff…). The app stores a
      *document reference*, never the document itself — upload the scans to
      object storage with short-lived signed URLs and let the provider do the
      verification. Then wire the provider's webhook to update `kyc_status`.
- [ ] **Appoint an AML officer** and write procedures for source-of-funds
      checks, suspicious-activity reporting and record retention (typically
      5 years). The app gives you the AML queue and the audit trail; the
      decisions are human and legally yours.
- [ ] **Decide your risk thresholds** — when to ask for source of funds, when to
      block a payout, when to report.
- [ ] **Set deposit/withdrawal limits** in `.env` that match your licence.

### 4. Infrastructure and security

- [ ] **Replace every default secret.** `SECRET_KEY` (the app refuses to start in
      production while it is still the default), `ADMIN_PASSWORD`, and the
      seeded admin email.
- [ ] **Move to PostgreSQL.** SQLite is the default so the app runs anywhere;
      it is not appropriate for concurrent real-money traffic.
      `DATABASE_URL=postgresql+psycopg://…`
- [ ] **Turn on HTTPS, always.** No exceptions. Set `CORS_ORIGINS` to your real
      domain instead of `*`.
- [ ] **Back up the database** continuously, test restores, and treat the ledger
      as the source of truth. Losing it means losing player balances.
- [ ] **Keep the audit log immutable.** Ship it to append-only storage off the
      application server so a compromised app cannot rewrite its own history.
- [ ] **Add a WAF, rate limiting and DDoS protection** in front of the API. The
      app rate-limits bets per minute, but that is one layer, not a defence.
- [ ] **Penetration test** before launch and after every significant change.
      Have a bug-bounty or disclosure address.
- [ ] **Monitor and alert** on: ledger sum ≠ 0, failed webhooks, payout queue
      depth, unusual win ratios, registration spikes.
- [ ] **Write an incident and disaster-recovery plan** — including what you do
      when a provider goes down mid-withdrawal.

### 5. Operations — the part that never ends

- [ ] **Staff the support desk** (chat, email, complaints). The app has a chat
      endpoint; a human has to answer it, and complaints usually have statutory
      response deadlines.
- [ ] **Staff the payout queue.** Someone has to review withdrawals with the AML
      queue, on weekends and holidays.
- [ ] **Set game maths you are willing to publish.** Every game's RTP is in the
      code and covered by tests; regulators and auditors will ask, and some
      jurisdictions require independent certification of the RNG and the maths
      (GLI-19 / eCOGRA / BMM). Budget for it.
- [ ] **Handle responsible gambling properly.** Limits, cool-off and
      self-exclusion exist; you still need staff training, intervention
      procedures and a link to a real help organisation.
- [ ] **Reconcile daily**: ledger vs. provider reports vs. bank vs. on-chain.
      The books must agree, and any disagreement is a stop-the-line incident.

### 6. What I could not do for you

- I cannot obtain a licence, sign a PSP contract or open a bank account.
- I cannot legally advise you on which jurisdiction or structure to use.
- I cannot verify the live payment adapters against a real merchant account —
  they are written to the providers' documented APIs and are covered by tests
  against a fake provider, but the first live transaction will be yours.
- The legal documents are templates with explicit placeholders, not law.

---

## How the money works

Every cent in this system is an **integer**. There is no floating-point money
anywhere in the backend, because `0.1 + 0.2 != 0.3` and casinos have lost real
money to exactly that.

### The ledger

Balances are not a number on a user row that code increments. They are the sum
of entries in a double-entry ledger, and **every transaction must sum to zero**
across its entries or the write is rejected:

```
account kinds:  user_available | user_bonus | user_locked
                house_revenue  | bonus_pool | payment_clearing
                rakeback_pool  | chargeback_loss | fee_income
```

Player funds can never go negative; system accounts can (they are the mirror
image of player money). `GET /api/wallet/integrity` and the admin health screen
both assert the global sum is zero, and the test suite asserts it after every
scenario. If it is ever non-zero, something is wrong and the app is designed to
make that loud.

#### Money types, and why they are integers

Amounts are **BIGINT minor units** — integer cents, integer ngultrum, integer
yen. Never floating point, and deliberately not `NUMERIC` either:

* every card scheme and wallet API takes and returns integer minor units
  (Adyen `amount.value`, Stripe `amount`, mBoB the same), so integer storage
  means an amount is never re-rounded on the way to the processor;
* the ledger invariant is *"entries sum to exactly zero"*. With integers that
  is an exact equality; with a decimal type it is a comparison you have to get
  the scale right on, in every query, forever;
* integer addition cannot lose a unit, so there is no rounding mode to argue
  about — because there is no rounding.

If you want decimals, they are already there. `backend/sql/schema.sql` publishes
two **NUMERIC views**, computed by exact NUMERIC division, which are read-only:

```sql
CREATE VIEW wallets AS       -- balance / locked / available as numeric(20,2)
CREATE VIEW transactions AS  -- one ledger posting: amount, balance_after
```

Storage stays exact and integer; every decimal an analyst, a BI tool or an
auditor reads is exact too. Both are validated by PostgreSQL's own parser in
the test suite, so a schema that does not parse fails the build rather than
failing on the database that holds the money.

#### Table names

| You asked for | Where it is |
|---|---|
| Users | `users` — auth, KYC status, limits, locale and currency |
| Wallets | `balances` (live balance per account) + `ledger_entries` (the record); read them as the `wallets` view |
| Transactions | `ledger_transactions` + `ledger_entries`; read them as the `transactions` view |
| BankingMethods | `banking_methods` — the banks added in the admin panel |

`create_all` builds these on first boot. On a real deployment run
`backend/sql/schema.sql` once, or adopt Alembic — the file is generated from the
models and a test fails if it drifts.

### Deposits

```
pending -> requires_action -> succeeded | failed | cancelled
                                    |
                                    +-> chargeback   (allowed; the only
                                                      terminal->terminal move)
```

A deposit is credited exactly once, guarded by an idempotency key, and only when
the provider says it settled. A chargeback debits the player and, if the balance
cannot cover it, flags and bans the account for review.

### Withdrawals

```
requested -> under_review -> approved -> paid
     |            |
     +------------+-> rejected | cancelled   (hold released back to the player)
```

When a player requests a withdrawal, the money leaves their spendable balance
immediately and sits in a held account. It only becomes real money out of your
business when a human approves it. Rejection puts it back. This is the
difference between a payout queue and a hole in your accounts.

### Bonuses

Bonus money is a separate account and cannot be withdrawn directly. It is
converted to withdrawable cash only by wagering it, weighted per game (slots
count 100%, roulette 20%, blackjack 10%), and the player can see exactly how
much is left to clear. Promo codes, rakeback and VIP tiers use the same
machinery.

---

## The games

11 games, each with a published return-to-player and a test that checks it. The
slot RTP is proven **exactly** by enumerating every possible symbol sequence
rather than by sampling, because a rare top prize makes sampled slot RTP
meaningless.

| Game | RTP | Notes |
|---|---|---|
| Dice | 99% | player picks the win chance, 0.01%–99.99% |
| Limbo | 99% | pick your multiplier |
| Mines | 99% | 5×5 grid, server-held board, cash out any time |
| Plinko | ~98.7% | 8/12/16 rows × 3 risk levels |
| Coin Flip | 99% | 1.98× |
| Crash | 99% | shared live round, auto cash-out, WebSocket feed |
| Roulette | 97.3% | European single-zero, full inside/outside bets |
| Wheel | ~96% | 54 segments, 3 risk profiles |
| Keno | ~96% | 80 numbers, exact hypergeometric paytable |
| Video Slots | 96.0% | 5 reels × 60 stops, 20 paylines, wilds + scatters |
| Blackjack | ~99.5% | 6 decks, S17, 3:2 blackjack, double/split, DAS |

House edge is deliberate and documented per game. Change a paytable and the
tests fail with the actual number — that is the point.

---

## Provably fair

Every bet is generated from `HMAC-SHA256(server_seed, client_seed:nonce)`. The
hash of the server seed is published before you play; the seed itself is revealed
when you rotate it, so the player can verify that past results could not have
been altered after the fact. Players can see their commitment in **Account →
Provably fair**, rotate the seed, and verify any past bet from **History**.

The crash game commits its seed when the round opens and reveals it when the
round busts.

---

## Compliance and player protection

Built in, because retrofitting these is far harder than building them:

- **Deposit and loss limits** the player sets themselves, enforced server-side.
  Limits can be tightened instantly, and loosened only after a cooling-off
  period — a player having a bad night cannot raise their own limit.
- **Cool-off and self-exclusion** with a server-side gate on betting.
- **KYC threshold** — withdrawals above a configurable lifetime amount require a
  verified identity.
- **Jurisdiction blocking** at registration.
- **Audit log** — every privileged action records who, what, before, after, when
  and from which IP.
- **AML queue** ranked by risk flags, with a human decision for every case.

---

## Deploying

### What this app needs that serverless platforms do not have

Being straight about this saves you a bad afternoon. The application was written
for a normal long-running server, and three of its assumptions clash with
serverless hosting:

| Assumption | Why serverless breaks it | What this repo does about it |
|---|---|---|
| A startup hook creates tables and the operator account | Vercel never calls the ASGI lifespan | the first request bootstraps the app (`bootstrap_app`), idempotently |
| A background loop runs the crash rounds | there is no process between requests, and ten instances would run ten different rounds | rounds advance from the **clock** on the request that arrives (`crash_loop.advance`), so shared state stays consistent with no loop |
| `./data/casino.db` is writable and keeps its contents | the bundle is read-only; only `/tmp` is writable and it is wiped | the database moves to `/tmp` automatically, and the API reports `balances_persist: false` so the UI can warn testers |

WebSockets are the one thing that cannot be faked: Vercel functions do not hold
long-lived connections. The crash game therefore refuses the socket with an
explanation and the page falls back to REST polling, which drives the exact same
state machine. Everything else is unaffected.

**This is a testing deployment.** Balances live in `/tmp` and reset when the
function is recycled. Point `DATABASE_URL` at a Postgres database (Neon and
Supabase both have free tiers) to make them survive.

### Deploy to Vercel

```bash
npx vercel login          # once
npx vercel                # preview deployment
npx vercel --prod         # production
```

Run `make deploy-check` first — it validates the config and runs the app the way
Vercel runs it.

Set these in **Project → Settings → Environment Variables**:

| Variable | Value | Why |
|---|---|---|
| `SECRET_KEY` | `python3 -c "import secrets;print(secrets.token_urlsafe(64))"` | without it each cold start mints a new key and every session drops |
| `DATABASE_URL` | `postgresql+psycopg://…` | optional; without it balances reset |
| `ADMIN_PASSWORD` | something real | the seeded default is public |
| `CORS_ORIGINS` | `https://your-app.vercel.app` | replace the `*` default |
| `ENVIRONMENT` | `production` | self-hosted only; enables the `SECRET_KEY` guard and the jurisdiction warning. On Vercel it is inferred — a Vercel deploy is treated as `staging` until you say otherwise, which is why one can look healthy while running without the guard |

**The "demo" banner.** A deployment where balances do not survive shows players
*"balances are not real money and may reset"*, and a DEMO badge beside the
balance. That is derived from whether they actually survive: without
`DATABASE_URL` on a serverless host it is on, and the moment you set
`DATABASE_URL` it goes off by itself — because from then on the claim would be
false. It is worth knowing that this used to be set for *every* serverless
deployment, so a production deploy with PostgreSQL still announced itself as a
test site with no way to turn it off.

The separate *"Sandbox payments: no real money moves"* notice keys off the
payment provider instead, and disappears when you set `PAYMENT_PROVIDER` to a
live one. The two questions — do balances persist, and is the money real — have
different answers and are shown separately.

Two honest caveats:

- **I could not run `vercel deploy` for you.** This build environment has no
  access to vercel.com (DNS resolves, connections are refused), so the last step
  has to run from your machine. Everything up to that point is verified: the
  app is exercised in a Vercel-shaped harness (`make smoke-serverless`, 60+
  checks) that removes the lifespan, removes the loops and moves the database to
  `/tmp`.
- **Vercel's terms are worth checking** before you host anything gambling-related
  there. This is a sandbox-mode test deployment with no real money; a licensed
  real-money operation should confirm the platform permits it, and will need a
  hosting setup with a persistent disk or a managed database regardless.

### How the routing is wired (read this before changing it)

Every request reaches one Python function, and two platform behaviours decide
whether that works:

1. **A bracketed filename is not a catch-all.** `api/[...path].py` compiles to
   `^/api/([^/]+)$` — exactly one segment after `/api`. `/api/health` reached
   Python; `/api/auth/register` (the signup endpoint) never did. The entry point
   is therefore `api/index.py`, a fixed path.
2. **A rewrite hands the function its destination, not the browser's URL.** So
   the rewrite has to carry the original path with it:

   ```
   /api/auth/register  ->  /api/index?__path=/api/auth/register
   /wallet             ->  /api/index?__path=/wallet
   ```

   The entry point reads `__path`, deletes it from the query string so the app
   sees exactly what the browser sent, and rewrites the ASGI scope.

Rules in `vercel.json` are evaluated in order, so the API rule is first, then
the page rules, then a catch-all: static files win over rewrites, so the built
site and its assets are still served from `public/` without invoking the
function at all. Six tests plus 30 routing checks in `make smoke-serverless`
resolve real URLs through the real `vercel.json`, static-file precedence
included, so this cannot silently regress.

### If something goes wrong after deploying

| Symptom | Cause | Fix |
|---|---|---|
| `/api/health` works but every deeper API path 404s | the function file is a **bracketed catch-all** (`api/[...path].py`), which Vercel compiles to a route matching one segment after `/api`; a higher-level push re-introduced the filename | the file must be `api/index.py`, reached by the `/api/(.*)` rewrite in `vercel.json`. `test_the_entry_point_is_not_a_bracketed_catch_all` and the routing section of `make smoke-serverless` both fail if it comes back |
| Every `/api/...` returns the platform's 404 | the rewrite dropped the original path (a rewrite hands the function its *destination*) | the rewrite must carry it: `/api/index?__path=/api/$1`. `test_multi_segment_api_paths_reach_the_function` enforces this |
| Page links return JSON instead of the page | something started prefixing page URLs with `/api/`, usually a "looks like an API call" heuristic | `test_the_entry_point_does_not_guess_paths` — the entry point must never infer a path; the rewrite states it |
| "Invalid session" after a while | `SECRET_KEY` is not set, so each cold start mints a new one | set `SECRET_KEY` in the project environment |
| Balances reset between visits | the database is the `/tmp` SQLite file | set `DATABASE_URL` to a Postgres URL |
| Crash page shows "Betting closed" but never runs | you are reading a cached response | check `GET /api/health` reports `deployment.background_loops` correctly; the browser sets `no-store` on API calls |
| Deploy fails on the Python function | dependency resolution in the build image | run `make deploy-check` locally first; it installs and runs the same entry point |

### The recommended shape for a real deployment

Vercel is an excellent home for the frontend and a poor one for this backend.
The split that works:

```
Vercel          ->  frontend (static build of frontend/dist)
Railway/Render/Fly/VM ->  backend (uvicorn, background loops, websockets)
Neon/Postgres   ->  the database (the ledger is the business)
```

To run the backend that way, deploy the repo with `backend/` as the root
directory and:

```bash
pip install -r backend/requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Everything works there exactly as it does locally: real background loops,
WebSockets, and a database that keeps its contents. Point the frontend at it
with `VITE_API_BASE` or a rewrite, and set `CORS_ORIGINS` to your frontend
domain.


---

## Configuration

All settings come from environment variables; see `backend/.env.example` for the
full annotated list. The safety-relevant defaults:

| Setting | Default | Meaning |
|---|---|---|
| `PAYMENT_PROVIDER` | `sandbox` | no real money moves |
| `AUTO_APPROVE_WITHDRAWAL_UNDER_USD` | `0` | every payout is human-reviewed |
| `KYC_REQUIRED_ABOVE_USD` | `1000` | lifetime withdrawals before ID is required |
| `JURISDICTION_MODE` | `allow_all` | no country is blocked until you say so |
| `GEO_ENFORCEMENT` | `false` | the edge's country header is not trusted by default |
| `SECRET_KEY` | insecure default | app refuses to boot in prod with it |
| `FIRST_DEPOSIT_BONUS_WAGER_X` | `30` | rollover on the welcome bonus |

### Banking methods — adding a payment rail without a deploy

Which bank, wallet or acquirer a player can use is a commercial decision, so it
is data. **Admin → Banking methods** adds, edits and switches off payment
pathways; the change is live for the markets it names on the next transaction,
with no release and no restart.

Each pathway carries a bank/method name, country code (ISO-3166 alpha-2, or `*`
for every country), currency (ISO-4217, or `*`), an account/merchant id, an
optional API endpoint, an optional credential variable, limits, a fee in basis
points, a priority and an active switch.

**How a pathway is chosen.** A pathway matches when it is active, its country is
the player's country or `*`, its currency is the transaction currency or `*`,
the direction is enabled, and the amount is inside its own bounds. Among the
matches, the most *specific* wins — `BT`/`BTN` beats `BT`/`*` beats `*`/`*` —
and priority breaks ties, then id. The same inputs always produce the same
answer, and **Banking methods → Why** prints the full ordered candidate list
with a reason against every row, which is the answer to "why did this player get
that bank".

The wildcard row is what keeps global access open: a `*`/`*` pathway at a high
priority number serves every market nobody has configured yet, so adding a
Bhutanese rail does not close the rest of the world. An unconfigured
deployment (an empty table) behaves exactly as it did before pathways existed.

**Credentials never enter the database.** The credential field takes the *name*
of an environment variable (`MBOB_API_KEY`), never the key — the API rejects
anything that looks like a secret. The panel reports whether that variable is
present in the running process, which is the one thing an operator needs to
debug a rail and the one thing the database cannot tell them.

**Nothing is ever deleted.** `Retire` deactivates. Settled transactions point at
their pathway forever, and a reconciliation report with holes in it is worse
than no report. Every change is audit-logged with before and after values.

**Two ways a rail settles.** If the pathway has an API endpoint, the adapter
pushes an initiation request to it, signed with the key in the named variable,
and shows the player whatever the rail says to do next. If it has no endpoint —
common for domestic rails — the player is shown the pathway's instructions and
a unique reference, and the deposit is credited when the rail's **signed
webhook** arrives at `/api/payments/webhooks/bank_transfer`, or when an operator
confirms it after the money appears on the statement. Both go through the same
idempotent credit path.

Callbacks are authenticated with HMAC-SHA256 over the raw body, keyed on
`BANK_TRANSFER_WEBHOOK_SECRET`. A missing secret means callbacks are *refused*,
not trusted. A callback that disagrees with the deposit's amount is refused
with a 422 and recorded, because a signature proves an event is authentic, not
that it is correct.

**Out-of-band payouts.** A rail with no payout API leaves an approved withdrawal
at `approved` with the funds still held. **Admin → Withdrawals → mark paid**
closes the loop, and requires the bank's transaction reference — "marked paid"
with no evidence is how a payout queue becomes unauditable. Until then the money
stays in `user_locked`, so it cannot be spent twice while a transfer is in
flight.

### Countries, languages and currencies

Three separate switches, deliberately not wired to each other:

**Which countries may play** is `JURISDICTION_MODE` plus a list. It ships open
(`allow_all`): nothing is blocked out of the box, because which markets you
accept players from is a licensing decision rather than a code decision. Set
`JURISDICTION_MODE=blocklist` with `JURISDICTION_BLOCKLIST=US,GB,...`, or
`allowlist` with the countries you are licensed for. `RESTRICTED_REGIONS` is
the middle tier — accounts and play are allowed, deposits and payouts are
refused with a specific message — which is what most operators actually want
for markets where a licence is pending. Whatever you set, player-protection
rules (age, KYC, AML flags, deposit and loss limits, self-exclusion) are
enforced independently and are not affected by it.

**What language a player sees** comes from the URL (`/es/wallet`), their stored
preference, or the region the edge reports — in that order. Nine locales ship
in `frontend/src/i18n/locales/` (`en`, `es`, `pt`, `de`, `fr`, `it`, `zh`,
`hi`, `ar`), with `ar` right-to-left. `npm run i18n:check` fails the build if a
locale is missing a key, drops a `{{placeholder}}`, or leaves a long string in
English; it compares plural forms against the language's CLDR categories, so
Arabic's six forms and Chinese's one are both correct rather than "inconsistent".

**What currency a player sees** is display only. The ledger settles in
`SETTLEMENT_CURRENCY` and stores integer minor units; `SETTLEMENT_CURRENCY`
cannot be changed by a locale, a country or a preference, and no bet is ever
priced in a display currency. `FX_RATES` (or the admin endpoint
`PUT /api/admin/fx-rates`) only changes what a figure is converted to on the
way to the screen, and every converted amount carries the settled figure in a
tooltip. Treat the shipped rates as placeholders: a stale rate is a
customer-facing promise, so wire them to your treasury feed before you take
real money.

**UI copy is translated; operator prose is not.** Button labels, table
headers, statuses and error states exist in all nine locales. The long-form
text — terms, privacy policy, responsible-gambling guidance, KYC instructions
— stays in `en` until a human translates and reviews it: a machine-translated
self-exclusion warning is a regulatory problem, not a cosmetic one.

---

## Testing

```bash
make test                                   # 194 tests
cd frontend && npm run check                # lint, i18n parity, build, render smoke
```

`npm run check` is the gate that matters for the UI. It runs ESLint, the
translation parity checker, a production build, and `scripts/render-smoke.mjs`
— which loads the real bundle in jsdom, stubs the API, and asserts that money,
statuses and calls to action actually render: in English at `/wallet`, in
Arabic (right-to-left) at `/ar/wallet`, and in Spanish on the account, bet
history and checkout pages, including the limits form behind a tab click. A
white screen, a raw `wallet.title` key or an untranslated money page fails it.

- `test_ledger.py` — double-entry invariants, zero-sum, insufficient funds
- `test_flows.py` — auth, deposits, withdrawals, admin review, bonuses,
  responsible gambling, the full happy path and the failure paths
- `test_game_math.py` — statistical RTP checks for every game
- `test_slots_exact.py` — **exact** slot RTP: closed form vs. brute-force
  enumeration of all 10⁵ symbol sequences, plus line-grader regression cases
- `test_deploy_config.py` — the deploy cannot drift from what was tested:
  requirements parity, Vercel config schema, the ignore rules, and the routing
  contract (a plain entry point, a rewrite that carries the original path, and
  no path guessing)

```bash
make smoke                                  # 50 checks against a running server
make smoke-serverless                        # 64 checks against the Vercel shape
```

---

## Project layout

```
backend/
  app/
    config.py          all settings
    money.py           integer-cent arithmetic (no floats, ever)
    models.py          schema: users, ledger, bets, payments, KYC, audit…
    ledger.py          double-entry posting, the only thing that moves money
    rng.py             HMAC provably-fair generator
    security.py        bcrypt, JWT, auth dependencies
    games/             11 game engines (pure functions, no I/O)
    services/          bets, wallet, payments, bonuses, compliance, crash loop
    payments/          provider interface + sandbox / stripe / cryptopay
    routers/           auth, wallet, games, crash, admin, misc
    main.py            app wiring, lifespan, background loops
  scripts/smoke_e2e.py end-to-end money-flow driver
  tests/               68 tests
frontend/
  src/pages/           Home, Login, Register, GameRoom, Wallet, Crash, Account,
                       History, Leaderboard, Promotions, Legal, Admin…
  src/pages/games/     11 game panels + shared stake control
  src/lib/             API client, formatters, auth/wallet store
```

Roughly 8,100 lines of backend Python, 5,400 of frontend JS/JSX, 1,400 of tests.

---

## Known gaps

Honest list of what is missing or not yet proven:

- **The live payment adapters have never touched a real merchant account.**
  Sandbox is fully exercised; Stripe and crypto are written to spec but untested
  against production APIs.
- **No email/SMS delivery.** Password resets and notifications are recorded in
  the app, but wiring an SMTP/SMS provider is yours to do.
- **No file upload pipeline.** KYC stores a document reference only; object
  storage and signed URLs are yours to add.
- **Single-process background loops** (crash engine, ops tasks). Before running
  more than one API process, move them behind a scheduler/lock.
- **No certified RNG.** The maths is provably correct and tested, but
  jurisdictions that require GLI-19/eCOGRA certification will need an audit.
- **Not load-tested.** No numbers for concurrent players or bets per second.
- **The spec PDF this was meant to follow never reached the build environment.**
  The system was built to standard industry architecture and behaviour instead.
  When the PDF is available, diff it against this build and adjust — the
  structure (games, ledger, payments, admin) is deliberately conventional so
  that gap-fitting should be additive rather than a rewrite.
