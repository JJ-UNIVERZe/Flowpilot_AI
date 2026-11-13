# Security notes

## Intended use and data

FlowPilot is a local fictional-data demo, not a production support platform.
The checked-in SQLite rows are fictional. Do not substitute real customer or
financial records without a privacy, retention, access-control, and
vendor-review process.

If OpenAI mode is selected, the customer message and structured lookup context
are sent to the configured OpenAI service. Only use that mode when the
applicable data-transfer policy permits it. The model cannot execute actions,
but a human reviewer must still check any proposed response before it can
proceed.

## Credentials and secrets

- Provide `OPENAI_API_KEY` and `REVIEW_API_KEY` through a secure process
  environment or secret manager. `.env.example` contains placeholders only and
  is not automatically loaded by the application.
- `.env` and `.env.*` are ignored by Git except the checked-in
  `.env.example`.
- `scripts/check_secrets.py` scans text files and available Git history and
  suppresses matching values. It is heuristic and is not a replacement for a
  managed secret scanner or credential rotation.
- The OpenAI verifier requires `--confirm-live-call`, sends one fixed prompt
  with no customer data, and does not print the response body or key. Its live
  service result is still UNVERIFIED.

## Review endpoint

The API should bind to loopback. The generation endpoint has no authentication;
the review endpoint fails closed when `REVIEW_API_KEY` is unset and rejects
missing or invalid keys. The key is sent in an HTTP header, so do not expose
the API over an untrusted network or plaintext transport.

The new `POST /agent/run` endpoint follows the existing unauthenticated
generation pattern and accepts a caller-supplied fictional customer ID. It
does not authenticate that ID; order tools only return details when the
requested order matches that ID. Do not treat this ownership check as a
production identity boundary. `GET /agent/runs/{id}`, agent approve/reject,
and `/metrics/summary` require the reviewer key.

The agent has only read-only order/refund tools. Refund details are gated by an
order-ownership lookup, malformed or extra tool arguments are rejected, and
tool attempts are logged in SQLite. The LLM has no operation to issue a refund,
modify an order, or send a customer message. The reviewer gate is still
mandatory; an approval marks the draft but does not execute an action.

The Streamlit interface sends the review key only with a review request and
does not display it. The key is a shared credential, not an individual
reviewer identity. The audit row records message, response, decision, and
timestamp, but not a verified reviewer identity.

The n8n webhook export is inactive and has no authentication or embedded
credentials. Keep it local and inactive unless deployment-specific
authentication, TLS, and network protections are added.

## Persistence and audit limitations

Order/refund/payment lookups do not write existing facts. Initialization can
create missing schema and fictional seed rows. Tool call logs, run metrics,
and approval decisions are stored in SQLite. Metrics do not store prompt or
draft text; approval logs do store the customer message and reviewed response.
Approval decisions are append-only under normal SQL operations due to SQLite
triggers, but a user with filesystem/database access can modify the schema or
database directly. Unreviewed API and agent runs/checkpoints exist only in
process memory and are lost on restart.

Live agent/evaluation requests require an explicitly selected live mode and
`OPENAI_API_KEY`; the agent API additionally requires `LLM_MODE=live` in its
process environment. Evaluation accepts `--live` or `LLM_MODE=live`. The
prompt/message may be sent to OpenAI. Mock mode makes no network requests.
Live cost estimates are only available when both configured
per-million-token price variables are provided; no price is assumed.

No endpoint or provider can issue refunds, change orders, or process payments.
The application has no production identity system, rate limiting, encrypted
database, or durable pending-review queue.
