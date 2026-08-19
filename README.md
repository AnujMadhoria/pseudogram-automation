# PseudoGram Comment-to-DM Automation

A reliable implementation of the LinkPlease assignment. A comment matching a creator rule is turned into one durable DM job per `(rule, user)`, then a worker sends and reconciles it with the hostile PseudoGram API.

## Architecture

```text
PseudoGram → POST /webhook → PostgreSQL events + jobs → worker → PseudoGram DM API
                                      ↓
                           GET /stats reads durable state
```

The API never waits for a DM API call in the webhook request. It records the event and queue state transactionally, responds quickly, and lets the worker perform sends and retries.

## Required endpoints

- `POST /rules` creates `{ "keyword": "PRICE", "dm_message": "..." }` and returns `201`.
- `POST /webhook` verifies the PseudoGram HMAC signature and records `comment.created` or `comment.deleted` events.
- `GET /stats` returns confirmed deliveries, permanent failures, active work, and durable duplicate-block decisions.
- `GET /health` is used by Render.

## Reliability guarantees

- `event_id` is a primary key, so redelivered webhooks do not create duplicate work.
- `(rule_id, recipient_user_id)` is unique in PostgreSQL, which prevents concurrent duplicate DMs.
- Every outbound send uses an `Idempotency-Key`; a crash or network timeout repeats the same logical attempt safely.
- A Postgres advisory lock and persisted rolling request log limit sends to 10 per 60 seconds.
- `500` and network errors retry with bounded exponential backoff. `429` obeys `Retry-After`. `400` fails immediately.
- A `202 Accepted` result is not counted as sent. The worker polls its DM status until it is delivered or fails, then retries failed deliveries within the configured budget.
- A deletion tombstone prevents a late `comment.created` event from causing a DM. Queued unsent jobs for deleted comments are cancelled.

## Local setup

1. Copy `.env.example` to `.env` and set `PSEUDOGRAM_API_KEY`. Never commit `.env`.
2. Start the API, worker, and local Postgres:

   ```powershell
   docker compose up --build
   ```

3. Create a rule:

   ```powershell
   Invoke-RestMethod -Method Post -Uri http://localhost:8000/rules -ContentType application/json -Body '{"keyword":"PRICE","dm_message":"Here is the price list"}'
   ```

4. Run tests with Python 3.12:

   ```powershell
   python -m pip install -r requirements.txt
   python -m pytest -q
   ```

## Render + Supabase deployment

1. Create a Supabase Postgres project and copy its **Session Pooler** connection string, including SSL.
2. Push this repository to GitHub and create a Render Blueprint from `render.yaml`.
3. The included Blueprint uses one **free Render Web Service** and runs the API and worker together. Set `DATABASE_URL` and `PSEUDOGRAM_API_KEY` as secrets; keep signature verification enabled.
4. Render creates a public URL for the Web Service. Use `<render-url>/webhook` as `webhook_url` for `POST /v1/simulate/start`.
5. Create rules, run the 500-comment simulator, then compare results with `GET /v1/simulate/{run_id}/truth`.

> Free Render services sleep after 15 minutes without incoming traffic. This mode is appropriate for development and short simulator runs, but a dedicated worker is required for fully reliable long-running production delivery.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | PostgreSQL SQLAlchemy connection URL |
| `PSEUDOGRAM_API_KEY` | Secret used for PseudoGram calls and webhook HMAC verification |
| `WEBHOOK_SIGNATURE_REQUIRED` | Keep `true` outside isolated local tests |
| `DM_MAX_ATTEMPTS` | Total outbound delivery attempts; default `5` |
| `WORKER_POLL_SECONDS` | Idle worker poll interval; default `0.5` |
| `RECONCILE_INITIAL_SECONDS` | First delivery-status poll delay; default `5` |
