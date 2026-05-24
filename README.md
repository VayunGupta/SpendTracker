# Spend Tracker

Self-hosted personal spend tracker that imports bank/card transactions through Plaid and Splitwise expenses, then computes your real spend after shared expenses.

Example: if your card shows an $80 dinner but Splitwise says your owed share is $16, the tracker reports $16 as your real spend.

## Why Plaid

Capital One, Discover, and PNC are normally connected through an aggregator rather than direct bank APIs. This project uses Plaid Link and Transactions Sync for that layer.

## Quick Start

1. Copy the env file:

   ```bash
   cp .env.example .env
   ```

2. Fill in Plaid and Splitwise credentials in `.env`.

3. Start the app:

   ```bash
   docker compose up --build
   ```

4. Open `http://localhost:8000`.

For a local restart that preserves connected Plaid accounts:

```bash
./scripts/restart_server.sh
```

Avoid deleting `data/spend_tracker.db` after connecting real accounts; it stores Plaid access tokens for already-linked banks. If you truly need to wipe local data, use `./scripts/reset_local_db.sh` and confirm the warning.

## Environment

| Variable | Purpose |
| --- | --- |
| `APP_SECRET_KEY` | Session signing secret. Change this before hosting. |
| `DATABASE_URL` | SQLite by default; can be changed to another SQLAlchemy URL later. |
| `PLAID_CLIENT_ID` | Plaid client id. |
| `PLAID_SECRET` | Plaid secret for sandbox/development/production. |
| `PLAID_ENV` | `sandbox` or `production`. Use `production` for real accounts, including limited production access. |
| `PLAID_PRODUCTS` | Defaults to `transactions`. |
| `PLAID_COUNTRY_CODES` | Defaults to `US`. |
| `PLAID_REDIRECT_URI` | Required for OAuth institutions. Use HTTPS for real bank connections. |
| `SPLITWISE_API_KEY` | Splitwise API key or OAuth bearer token. |
| `SPLITWISE_USER_ID` | Your Splitwise user id, used to compute your owed share. |

## Real Bank Connections

Plaid real-data OAuth flows require an HTTPS redirect URI. Keep the app running locally on HTTP, then expose it through an HTTPS tunnel:

```bash
ngrok http 8000
```

Use the HTTPS forwarding URL as your Plaid redirect URI:

```env
PLAID_ENV=production
PLAID_REDIRECT_URI=https://your-tunnel-host.ngrok-free.app/oauth/plaid
```

Add that exact URL to Plaid Dashboard's allowed redirect URIs, then restart the app.

## Model

The app stores raw source data and derives adjusted spend:

- Bank transaction: what actually hit your card/checking account.
- Splitwise expense: shared expense state, including your owed share.
- Adjusted spend: bank amount replaced by your Splitwise share when a likely match exists.
- Splitwise-only spend: money you owe for expenses somebody else paid.

The matcher is conservative: same amount, nearby date, and similar merchant/description. You can extend this with manual matching once real data starts flowing.

Splitwise sync is incremental. The first successful sync pulls existing expenses, then later syncs request only expenses updated after the previous successful sync.

## Current Scope

This is an MVP foundation:

- Connect bank accounts/cards with Plaid Link.
- Sync bank transactions with `/transactions/sync`.
- Sync Splitwise expenses from the official API.
- View raw and adjusted spend in a browser.
- Recompute actual spend from stored source data.

Next useful additions are manual matching, categories/budgets, recurring rules, and background sync.
