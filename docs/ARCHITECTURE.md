# Architecture

## Request and response flow

```text
CLI ─────────────────────────┐
Streamlit UI -> FastAPI      ├─> classify/validate -> one SQLite lookup
n8n -> FastAPI               │       -> General Inquiry policy retrieval
                             │       -> template or selected provider
                             │       -> factual/policy validation -> fallback
                             └───────> Pending response -> explicit human review
                                                       -> SQLite approval audit
```

The interfaces call the same workflow functions rather than reimplementing
classification or data access:

1. `flowpilot.py` normalizes and classifies the customer message.
2. The workflow extracts a supported order ID and performs at most one relevant
   lookup. The result dictionary is reused for response generation.
3. Only General Inquiry searches the local policy folder. Template mode cites
   the top passage; mock and OpenAI modes receive delimited passages as
   untrusted data. Other categories do not use the policy index.
4. Provider candidates are checked against workflow facts and, for General
   Inquiry, policy numbers and promises are checked against retrieved text.
   Missing passages, provider errors, empty text, and rejected claims use the
   existing template response.
5. CLI review, the FastAPI review endpoint, or Streamlit can record a decision.
   Generation alone always leaves `approval_status` Pending and
   `can_proceed` false.

`flowpilot.py` imports the template generator; the template generator may
optionally import the LLM provider. The LLM module imports template helpers
only inside its fallback function, after module initialization. This avoids an
import-time cycle; the reload-and-fallback regression test exercises that path.
Provider-assisted workflow reuses the already-fetched result instead of
performing a second database lookup.

## Provider and prompt boundaries

`OpenAIProvider` uses the official OpenAI Python SDK. Its API key and model are
read from `OPENAI_API_KEY` and `OPENAI_MODEL`; credentials are not included in
prompts or log output. The prompt separates system instructions from the
customer/context message. Customer content is untrusted input and cannot
override system rules.

The model only drafts text. It has no database write interface or tools for
issuing refunds, changing orders, or processing payments. Regex-based factual
and policy validation are defensive filters, not semantic proofs. Retrieved
passages and customer messages are data, never instructions. Mandatory human
review remains in place.

`GET /kb/search?q=...` requires the configured reviewer key, limits queries to
200 characters, and returns at most three local passages without modifying
data. `/process` and the existing `/workflow` endpoint return the same Pending
workflow result.

## SQLite and audit records

`database.py` stores fictional sample orders and the `approval_logs` table.
Existing order/refund/payment facts are not rewritten during initialization or
lookups. Missing local tables and fixture records can be created as part of
database initialization.

Approval decisions are append-only under ordinary SQL operations: SQLite
triggers reject updates and deletes to `approval_logs`. A privileged operator
can still alter the database schema or remove the triggers, so this is not a
tamper-proof external audit service.

The API keeps unreviewed response payloads in process memory. A restart loses
pending items; completed review decisions persist in SQLite. This is suitable
for a local demo, not a durable queue.

## API security boundary

The API is intended to bind to `127.0.0.1`. Review decisions require a
configured `REVIEW_API_KEY` and a matching `X-Review-API-Key` header. The key
is compared in constant time and is never returned in API errors. The review
route accepts only Approved or Rejected decisions and writes through the
existing approval audit function.

Generation is not authenticated, and the sample n8n webhook is also
unauthenticated. Do not expose either service outside a trusted local
environment without adding authentication, TLS, rate limits, and deployment
controls.
