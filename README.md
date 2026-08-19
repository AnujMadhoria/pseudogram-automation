# PseudoGram Comment-to-DM Automation — Part A

This project implements the required Part A of the LinkPlease assignment: a creator creates a keyword rule, comments are matched against it, and a reliable background worker sends one DM per rule/user.

## What happens

```text
PseudoGram comment webhook
  → FastAPI validates and saves the event
  → PostgreSQL creates a durable DM job
  → worker sends the DM with retry and idempotency protection
  → GET /stats reports the final Part A send state
```

The webhook never waits for the DM API call. It saves work first and returns `200` quickly.

## Required endpoints

- `POST /rules`

  ```json
  { "keyword": "PRICE", "dm_message": "Here is the price list." }
  ```

  Returns `201` with `rule_id`, `keyword`, and `dm_message`.

- `POST /webhook`

  Receives `comment.created` events. Matching is case-insensitive and works anywhere in the comment text.

- `GET /stats`

  ```json
  { "sent": 0, "failed": 0, "queued": 0, "duplicates_blocked": 0 }
  ```

- `GET /health`

  Used only by Render to check that the application is alive.

## Part A reliability guarantees

- A duplicate `event_id` is ignored.
- A database unique constraint prevents the same `(rule, user)` pair from receiving two DMs.
- Every matching DM is stored in PostgreSQL before a send is attempted.
- The worker retries network errors and `500` responses up to five attempts.
- `429` waits for the API-provided retry time.
- A persisted rate log stays within PseudoGram's 10-send-per-60-second limit.
- Every retry uses the same idempotency key, so an uncertain network failure cannot create a duplicate DM.

For Part A, a successful `2xx` response from the mock DM API is counted as `sent`. The live mock returns `200` although the brief documents `202`, so both are supported.

## Deliberately out of scope

- Webhook signature verification
- Delivery-status polling after the mock accepts a DM
- Retrying DMs that fail later after initial acceptance
- Comment-deletion behavior

Those are Part B/C features. `comment.deleted` webhooks are accepted and recorded but do not create a DM job.

## Local setup

1. Copy `.env.example` to `.env` and fill in `PSEUDOGRAM_API_KEY`.
2. Start local Postgres, API, and worker:

   ```powershell
   docker compose up --build
   ```

3. Run tests:

   ```powershell
   python -m pytest -q
   ```

## Render + Supabase

`render.yaml` creates one free Render Web Service. `start-free-render.sh` starts both the API and worker process in that service. Add `DATABASE_URL` and `PSEUDOGRAM_API_KEY` as Render secrets.

Free Render services can sleep after 15 minutes without incoming traffic, so this deployment is suitable for development and short simulations rather than long-running production queues.
