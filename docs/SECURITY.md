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

The Streamlit interface sends the review key only with a review request and
does not display it. The key is a shared credential, not an individual
reviewer identity. The audit row records message, response, decision, and
timestamp, but not a verified reviewer identity.

The n8n webhook export is inactive and has no authentication or embedded
credentials. Keep it local and inactive unless deployment-specific
authentication, TLS, and network protections are added.

## Persistence and audit limitations

Order/refund/payment lookups do not write existing facts. Initialization can
create missing schema and fictional seed rows. Approval decisions are
append-only under normal SQL operations due to SQLite triggers, but a user
with filesystem/database access can modify the schema or database directly.
Unreviewed API responses exist only in process memory and are lost on restart.

No endpoint or provider can issue refunds, change orders, or process payments.
The application has no production identity system, rate limiting, encrypted
database, or durable pending-review queue.
