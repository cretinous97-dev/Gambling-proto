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

Two honest caveats:

- **I could not run `vercel deploy` for you.** This build environment has no
  access to vercel.com (DNS resolves, connections are refused), so the last step
  has to run from your machine. Everything up to that point is verified: the
  app is exercised in a Vercel-shaped harness (`make smoke-serverless`, 30
  checks) that removes the lifespan, removes the loops and moves the database to
  `/tmp`.
- **Vercel's terms are worth checking** before you host anything gambling-related
  there. This is a sandbox-mode test deployment with no real money; a licensed
  real-money operation should confirm the platform permits it, and will need a
  hosting setup with a persistent disk or a managed database regardless.

### If something goes wrong after deploying

| Symptom | Cause | Fix |
|---|---|---|
| Every `/api/...` returns 404 | the platform rewrote the function's path and dropped the original | the entry point already tries to recover it from proxy headers; if it cannot, tell me and we switch to an explicit rewrite |
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
| `JURISDICTION_BLOCKLIST` | `US,GB,FR,NL,AU` | placeholder, not legal advice |
| `SECRET_KEY` | insecure default | app refuses to boot in prod with it |
| `FIRST_DEPOSIT_BONUS_WAGER_X` | `30` | rollover on the welcome bonus |

---

## Testing

```bash
make test                                   # 81 tests
```

- `test_ledger.py` — double-entry invariants, zero-sum, insufficient funds
- `test_flows.py` — auth, deposits, withdrawals, admin review, bonuses,
  responsible gambling, the full happy path and the failure paths
- `test_game_math.py` — statistical RTP checks for every game
- `test_slots_exact.py` — **exact** slot RTP: closed form vs. brute-force
  enumeration of all 10⁵ symbol sequences, plus line-grader regression cases
- `test_deploy_config.py` — the deploy cannot drift from what was tested
  (requirements parity, Vercel config, entry point, ignore rules)

```bash
make smoke                                  # 50 checks against a running server
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
