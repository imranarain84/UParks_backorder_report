# Vertical Passage — Universal Parks Daily Pre-Order Report

Finds orders that are **stuck on backorder** where the SKU has **no open
replenishment PO at all** — i.e. stock we haven't even ordered more of yet.
It's limited to smaller-scale issues (fewer than `MAX_ORDERS_PER_SKU`
affected orders per SKU, default 5); larger stockouts are assumed to
already be known/handled and are excluded on purpose.

This is a straight port of the working Snow Commerce backorder report to
Universal Parks' own ShipHero account. All of the ShipHero query logic,
retry/credit-handling, on-order calculation, Gmail sending, and the GitHub
Actions workflow structure are unchanged — only the client-specific bits
(credentials, branding strings, recipients) differ. It:

1. Pulls open orders from ShipHero (GraphQL public API)
2. Pulls open Purchase Orders
3. Finds backordered line items whose SKU has **no** matching open PO
4. Drops any SKU affecting `MAX_ORDERS_PER_SKU` (default 5) or more distinct
   orders — those are treated as already known at that scale
5. Drops any order that's already tagged `preorder` (someone has already
   seen and handled it)
6. Writes the remaining rows to a CSV
7. Emails the CSV to a distribution list via Gmail

This is **read-only against ShipHero** — the automation does not write the
`preorder` tag itself. Tagging an order is what takes it off tomorrow's
report, so however you choose to apply that tag (manually in the ShipHero
UI, a separate process, etc.) doubles as your "handled" signal.

Runs daily on a schedule via **GitHub Actions** — no server to maintain.

---

## How the matching works

ShipHero doesn't have a single "backorder" order status you can filter on.
Instead, each order line item has a `backorder_quantity` field. This tool:

- Fetches orders that are not yet fully fulfilled, over a configurable
  lookback window
- Keeps only line items where `backorder_quantity > 0`
- Fetches open Purchase Orders (`fulfillment_status` not `closed`)
- For each PO line item, computes `quantity - quantity_received` — if
  that's > 0, that SKU has inbound replenishment
- Any backordered line item whose SKU has **no** entry in that open-PO set
  is a candidate
- Candidates are grouped by SKU; any SKU with `MAX_ORDERS_PER_SKU` (default
  5) or more distinct affected orders is dropped entirely — the assumption
  is that a stockout affecting that many orders is already visible/being
  worked some other way, and this report exists to catch the smaller ones
  that are easy to miss
- Of what's left, orders already tagged `preorder` are excluded

Because the tag itself is the "already reported" marker, an order naturally
drops off the daily report once it gets tagged — there's no separate
dedup/state tracking needed.

## Repo layout

```
src/
  config.py               # env-driven settings
  shiphero_auth.py         # refresh-token -> access-token exchange
  shiphero_client.py       # GraphQL calls: orders, purchase_orders, on-order lookup
  matcher.py                # backorder <-> open PO matching logic
  csv_export.py             # writes the daily CSV
  gmail_client.py           # sends the email with the CSV attached via Gmail API
  main.py                    # orchestrates the whole run
scripts/
  get_gmail_refresh_token.py # one-time local script to mint a Gmail refresh token
.github/workflows/
  daily-preorder-report.yml  # the daily cron
```

## One-time setup

### 1. ShipHero refresh token for Universal Parks

Universal Parks needs its **own** ShipHero refresh token — this must not
be the same token used for Snow Commerce or any other client's report.

```bash
curl -X POST -H "Content-Type: application/json" -d \
  '{"username":"YOUR_EMAIL","password":"YOUR_PASSWORD"}' \
  "https://public-api.shiphero.com/auth/token"
```

Save the `refresh_token` from the response — that's what the script uses.
It does **not** rotate on refresh, so you set it once as a GitHub secret and
you're done (access tokens expire every 28 days, but the script re-derives
a fresh one every run, so you never touch this again unless you revoke it).

Recommended: create a **dedicated third-party developer user** in ShipHero
(Dashboard → Users → "+Add Third-Party Developer") instead of using your own
login, so this automation isn't tied to your personal credentials.

### 2. `customer_account_id` and `warehouse_id` — check, don't assume

Both of these are **filters** — they only matter if you want to narrow the
report to a subset of Universal Parks' account data. Don't carry over
whatever Snow Commerce needed (or didn't need) — check fresh:

- **`customer_account_id`** — only relevant if Universal Parks' ShipHero
  account is a 3PL parent account acting on behalf of multiple customer
  sub-accounts. If the token is already scoped to a single account (check
  with the query below), skip it.
- **`warehouse_id`** — only relevant if you want the report limited to one
  specific warehouse. If orders could ship from any warehouse on the
  account, skip it and every warehouse gets included. Note: if the account
  has multiple warehouses that are all confusingly labeled the same way
  (e.g. all named "Primary"), the label alone won't tell you which is
  which — you'll need the actual `warehouse_id` values, and it's worth
  deciding up front whether Universal Parks' use case needs single-warehouse
  scope or should span the whole account.

To check which situation you're in, run this in Terminal (swap in a current
access token — see the auth section above for how to get one):

```bash
curl -X POST -H "Content-Type: application/json" -H "Authorization: Bearer YOUR_ACCESS_TOKEN" -d '{"query":"query { account { data { id is_3pl email } } }"}' "https://public-api.shiphero.com/graphql" | python3 -m json.tool
```

If `is_3pl` is `false`, you almost certainly don't need either value —
leave both `SHIPHERO_CUSTOMER_ACCOUNT_ID` and `SHIPHERO_WAREHOUSE_ID` out of
your GitHub secrets entirely, and the report will pull all backordered
orders and all open POs across the whole account.

### 3. Gmail API OAuth (one-time, run locally — not in GitHub Actions)

You can reuse the same Gmail Cloud Console project/OAuth client used for
Snow Commerce if you want the reports coming from the same sender address;
otherwise set up a fresh one:

1. In Google Cloud Console, create a project, enable the **Gmail API**, and
   create an **OAuth Client ID** of type "Desktop app". Download the
   `client_secret.json`.
2. Locally:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_gmail_refresh_token.py --client-secret client_secret.json
   ```
3. This opens a browser, you approve access to send mail as yourself, and it
   prints a `refresh_token`. That token, plus the client ID/secret, are what
   go into GitHub Secrets. This only needs to be done once.

### 4. GitHub repo secrets

Add these under Settings → Secrets and variables → Actions (use a **new**
repo, or new secrets, separate from the Snow Commerce one — don't overwrite
those):

| Secret | Value |
|---|---|
| `SHIPHERO_REFRESH_TOKEN` | Universal Parks' token, from step 1 |
| `SHIPHERO_CUSTOMER_ACCOUNT_ID` | **optional** — skip entirely unless Universal Parks' account is a 3PL parent account (see step 2) |
| `SHIPHERO_WAREHOUSE_ID` | **optional** — skip entirely unless you want to limit the report to one warehouse (see step 2) |
| `GMAIL_CLIENT_ID` | from step 3 |
| `GMAIL_CLIENT_SECRET` | from step 3 |
| `GMAIL_REFRESH_TOKEN` | from step 3 |
| `GMAIL_SENDER_EMAIL` | the Gmail address sending the report |
| `REPORT_RECIPIENTS` | comma-separated distribution list for Universal Parks' backorder report |

### 5. Adjust the schedule

Edit `.github/workflows/daily-preorder-report.yml` — the `cron` line is in
UTC. It's currently set to 11:00 UTC (7:00am ET / 6:00am during EDT — adjust
for daylight saving as needed, GitHub Actions cron doesn't auto-shift).
Consider staggering this from Snow Commerce's run time if both hit the same
Gmail sender or overlapping ShipHero infrastructure.

## Running locally (for testing before you rely on the schedule)

```bash
cd universal-parks-backorder-report
pip install -r requirements.txt
export SHIPHERO_REFRESH_TOKEN=...
export SHIPHERO_CUSTOMER_ACCOUNT_ID=...   # optional — omit this line entirely if not applicable
export SHIPHERO_WAREHOUSE_ID=...          # optional — omit this line entirely if not applicable
export GMAIL_CLIENT_ID=...
export GMAIL_CLIENT_SECRET=...
export GMAIL_REFRESH_TOKEN=...
export GMAIL_SENDER_EMAIL=...
export REPORT_RECIPIENTS=you@verticalpassage.com
python src/main.py --dry-run   # writes the CSV and prints what it would email, but doesn't send anything
python src/main.py             # full run: writes CSV + sends email
```

## Things worth double-checking before first real run

- **Confirm the enum/field names still match Universal Parks' ShipHero
  schema version.** These fields were confirmed working against Snow
  Commerce's account; GraphQL Playground against Universal Parks' own
  account (Schema tab) is the source of truth if anything errors out with
  a field-not-found message. If you hit an unknown-field error, run an
  introspection query rather than guessing:
  ```graphql
  query { __type(name: "TypeNameHere") { name fields { name type { name kind ofType { name kind } } } } }
  ```
- **Credits/throttling**: ShipHero rate-limits by query "complexity"
  (roughly, how much data a query touches), refilled gradually over time,
  and this can be especially volatile on accounts with real concurrent
  traffic (webhooks, other integrations, warehouse ops) — the same shared
  credit pool that Universal Parks' WeSupply/ShipHero returns webhook
  integration draws from. If both integrations run around the same time,
  expect more retries. The client here paginates in small batches (5 orders
  per page, 10-15 line items each) to keep each request cheap, and
  automatically waits and retries (up to 30 times) using ShipHero's exact
  reported wait time rather than guessing.
- **Lookback window**: defaults to **7 days** (`LOOKBACK_DAYS`). Snow
  Commerce tuned this and `MAX_ORDERS_PER_SKU` (5) for its own order
  volume — Universal Parks' volume may call for different values. Start
  with the defaults, watch a few days of `--dry-run` output, and adjust.
