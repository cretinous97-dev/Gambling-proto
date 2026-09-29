# Going live — what I need from you, and in what order

Read section 1 before you paste anything into this chat.

---

## 1. How to give me secrets (please read)

This chat is not a secure channel. Do not paste the API keys of a live payment
provider here. Instead:

1. **Put the value in the Vercel dashboard yourself** — Project → Settings →
   Environment Variables. I do not need to see them; the app reads them at
   runtime.
2. If you need me to debug something, paste the **error message**, not the key.
3. For local testing, put them in `backend/.env` on your machine
   (`backend/.env` is gitignored — verify with `git check-ignore -v backend/.env`).

What is genuinely safe to paste here: the **shape** of a value
(e.g. `postgresql+psycopg://user:***@ep-xxx.neon.tech/db`), hostnames,
project names, error text, and anything already public.

**Important:** knowing a database URL is enough to withdraw money from it. If
you ever do paste one with its password, rotate that password afterwards.

---

## 2. What I need from you, grouped by what it unlocks

### Group A — to deploy for testing (nothing else required)

You have already picked Vercel. Nothing needs to be pasted here; you set these
in the Vercel dashboard:

| Variable | Where to get it | Consequence if missing |
|---|---|---|
| `SECRET_KEY` | Step 1 below | **not mandatory** (see the table under Step 4), but sessions can drop |
| `ADMIN_PASSWORD` | choose one | the seeded `Admin!2345` is public knowledge |
| `CORS_ORIGINS` | `https://your-app.vercel.app` | default `*` allows any site to call your API |
| `DATABASE_URL` | see Group B | balances reset when the function is recycled |
| `PAYMENT_PROVIDER` | `sandbox` for now | stays in test mode — correct until Group C |

That is the whole list for a working test deployment. **The site works today
without you pasting me anything.**

### Group B — to stop balances resetting (30 seconds, free)

Right now the deployed database lives in `/tmp` and is wiped when the function
recycles. That is fine for a demo and useless for real money.

1. Create a free Postgres at **neon.tech** or **supabase.com**.
2. Copy the connection string.
3. Convert it to the SQLAlchemy form — this trips everyone up:

```
what they give you:  postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require
what this app wants: postgresql+psycopg://user:pass@ep-xxx.neon.tech/neondb?sslmode=require
                              ^^^^^^^^^  add this
```

4. Add it as `DATABASE_URL` in Vercel and redeploy. Confirm by visiting
   `/api/health` — `deployment.balances_persist` must flip to `true`.

The schema is created automatically on first request. No migrations to run.

### Group C — to accept real money (this is the big one)

Real money needs a **payment provider account that you own and are approved
for**. There is no way around this and I cannot do it for you: providers
onboard businesses, not developers, and gambling merchants need approval that
takes days to weeks.

**The hard part first:** most providers will not process gambling. Open an
account with one that will, or with your existing acquirer:

| Provider | What you get from them | What I need (in Vercel, not here) |
|---|---|---|
| Stripe (gambling programme) | secret key, webhook signing secret | `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` |
| Crypto processor (e.g. Coinbase Commerce, NowPayments, Cryptomus) | API key, webhook secret | `CRYPTOPAY_API_KEY`, `CRYPTOPAY_WEBHOOK_SECRET` |
| Your acquirer / PSP | API docs — I may need to write an adapter | send me the docs link |

Also needed:

- **Your webhook URL**, registered with the provider:
  `https://your-app.vercel.app/api/wallet/webhooks/{provider}`. The app rejects
  unsigned callbacks, so the signing secret must match.
- **A payout destination**: for crypto, the wallet address you will pay out
  from; for cards, your merchant account.

Then flip `PAYMENT_PROVIDER` from `sandbox` to `stripe` or `cryptopay`. The
simulate buttons disappear automatically (the endpoint returns 403), and the
"sandbox" banner clears.

### Group D — to pass even a light touch of scrutiny

| Item | Why it matters | What I need |
|---|---|---|
| KYC provider account (Sumsub, Onfido, Veriff) | withdrawals must be tied to a verified human | API key + webhook secret, and I will wire the callback |
| Email delivery (Postmark, SES, Resend) | password resets and payment notices | SMTP credentials |
| Your real domain | trust, and `CORS_ORIGINS` | the domain, and access to its DNS |
| Object storage for KYC scans (S3/R2) | documents must never touch the app database | bucket + keys |

### Group E — facts only you have

These change what the software must do, so tell me and I will adjust it:

1. **Which licence, from which regulator?** (MGA, Curaçao, Anjouan, Isle of
   Man, a US state, other) — it dictates reporting, self-exclusion registers,
   and display requirements.
2. **Which countries will you accept players from?** The blocklist currently
   defaults to `US,GB,FR,NL,AU`, which is a placeholder, not advice.
3. **Company name, address, licence number, regulator address** — these go into
   the legal pages, which currently contain `OPERATOR MUST INSERT`.
4. **Maximum win per bet** and **maximum bet** you are willing to fund. Current
   defaults: max bet $2,000, max win $100,000.
5. **Withdrawal policy**: fee, minimum, and whether any payout auto-approves.
   Currently every payout needs a human, and that is the safe default.

---

## 3. Step by step to a working live site

### Step 1 — deploy the test site (10 minutes, no keys needed)

```bash
git clone https://github.com/cretinous97-dev/Gambling-proto
cd Gambling-proto
npx vercel login
npx vercel --prod
```

In Vercel → Settings → Environment Variables, add `SECRET_KEY`,
`ADMIN_PASSWORD`, `CORS_ORIGINS`. Redeploy. Open the URL.

Check: the lobby shows generated artwork; register; deposit; press
**Simulate success**; play; withdraw; approve as admin.

### Step 2 — make it persistent (5 minutes)

Do Group B above. Re-check `/api/health` for `balances_persist: true`.

### Step 3 — connect real payments (1–2 weeks, mostly waiting on approval)

1. Apply for the merchant account. **Start this today** — it is the long pole.
2. While waiting: send me the provider's API docs if it is not Stripe or a
   crypto processor, and I will write the adapter.
3. When approved, put the keys in Vercel, register the webhook, flip
   `PAYMENT_PROVIDER`.
4. **Test with the provider's test mode first.** Then move a small real amount
   through end to end: deposit $10, withdraw $5, approve it, confirm the money
   actually arrives. Reconcile the ledger against the provider's report.

### Step 4 — before you accept your first real player

- [ ] A licence you can show the provider (they will ask before they approve you)
- [ ] Real legal pages (replace every `OPERATOR MUST INSERT`)
- [ ] Player funds in a segregated account — **never** the operating account
- [ ] KYC provider wired up
- [ ] Email delivery working
- [ ] Postgres backups on, and a **restore you have actually tested**
- [ ] Someone on call for the payout queue, including weekends
- [ ] `/api/wallet/integrity` monitored: the books must sum to zero, always

---

## 4. Where the money actually is — and what "the house always wins" means

The maths is done and verified, so you should know exactly what you own.

Every game has a published return. The margin is the gap between return and
100%:

| Game | Return | Your margin | Verified by |
|---|---|---|---|
| Dice, Limbo, Mines, Coin Flip, Crash | 99% | 1% | statistical test |
| Blackjack | ~99.5% | ~0.5% | payout table test |
| Roulette | 97.3% | 2.7% | 36/37, exact |
| Plinko | ~98.7% | ~1.3% | statistical test |
| Wheel | ~96% | ~4% | segment table |
| Keno | ~96% | ~4% | exact hypergeometric |
| Slots | 96.0% | 4.0% | **exact**, all 10⁵ sequences |

**"The house always wins" is true over thousands of bets and false on any given
night.** That is not a flaw, it is how gambling maths works, and it is why the
next paragraph matters.

### The three things that actually lose operators money

1. **The tail.** House edge does not protect you from one maximum-stake top-prize
   hit. Fixed: settlements are now capped at `MAX_WIN_USD` (default $100,000),
   recorded on the bet so it is visible rather than silent.
2. **Bonuses.** A 100% match bonus with a 30× rollover sounds profitable until
   you check it: the player wagers $3,000 to clear a $100 bonus, and at a 4%
   house edge you expect $120 against their $100 stake. **That is a loss of
   about $20.** Set `FIRST_DEPOSIT_BONUS_PCT=0` until you have modelled your own
   bonus against your own game mix, or raise the rollover.
3. **Payment costs and chargebacks.** Card fees plus a chargeback rate above
   roughly 1% will exceed a 1% margin on deposits alone. The app absorbs a
   chargeback by debiting the player and banning the account if the balance
   cannot cover it — that protects your books, not your processor relationship.

### What the ledger guarantees

Balances are not a number on a user row. Every movement is a double-entry
transaction that must sum to zero, or the write is rejected. Deposits are
credited exactly once, guarded by an idempotency key. Withdrawals move money
into a held account and only release it on a human approval. If the books ever
fail to balance, `/api/wallet/integrity` and the admin dashboard both say so —
loudly, on purpose.
