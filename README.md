..# FlowPilot AI

FlowPilot AI is a local customer-support workflow demo. It classifies a
fictional customer message, looks up sample order/refund/payment facts, drafts
a response, and blocks further workflow progress until a human explicitly
approves it.

It does **not** send customer messages, issue refunds, process payments, or
change orders. Order/refund/payment lookup functions read existing facts;
database initialization only creates missing local sample data or schema.

## Setup

Use Python 3.10 or newer. Install runtime dependencies:

```text
python -m pip install -r requirements.txt
```

For development, tests, coverage, and lint:

```text
python -m pip install -r requirements-dev.txt
```

The application reads configuration from process environment variables. It
does not automatically load `.env`; `.env.example` contains placeholders and
defaults only. Never commit a real `.env` or paste a credential into shell
history.

## CLI

Run the local sample demo:

```text
python flowpilot.py
```

Run a message through a specific response mode:

```text
python flowpilot.py --mode template --message "My order ORD1042 has not arrived."
python flowpilot.py --mode mock --message "My order ORD1042 has not arrived."
python flowpilot.py --mode openai --message "My order ORD1042 has not arrived."
```

Template mode is the default. Mock mode is offline. OpenAI mode requires
`OPENAI_API_KEY` and can send request content to OpenAI; use it only when that
data transfer is approved. `OPENAI_MODEL` is optional and defaults to
`gpt-4o-mini`. The legacy positional-message form and `--llm` alias remain
supported. Add `--db PATH` to select a local SQLite file and `--json` to export
the result to `workflow_result.json`.

Every interactive CLI response requires a human decision. Without a reviewer
decision, the result stays Pending and cannot proceed.

Run the built-in classifier checks:

```text
python flowpilot.py --classify-tests
```

Run the offline labelled evaluation:

```text
python scripts/evaluate.py --mode template
python scripts/evaluate.py --mode mock
```

The evaluator uses fictional cases and a temporary SQLite database. It never
calls OpenAI or requests human approval. See
[docs/EVALUATION.md](docs/EVALUATION.md) for the latest measured results.

## Policy lookup (RAG)

General Inquiry replies can use local fictional Markdown policies in `kb/`.
The retriever is pure Python (BM25), makes no network or embedding calls, and
adds source titles to the reply. Policy passages are treated as data; unsupported
numbers and promises are rejected, and a human still makes every final decision.
If no passage is found, the existing General Inquiry template is used.

The authenticated, read-only `GET /kb/search?q=...` endpoint returns up to
three passages and requires `X-Review-API-Key`. The `/process` route is an alias
for `/workflow`; both keep generated responses Pending. See
[docs/KNOWLEDGE_BASE.md](docs/KNOWLEDGE_BASE.md) for document and evaluation
guidance.

## FastAPI and Streamlit

Configure `REVIEW_API_KEY` securely in the API process, then start the API on
loopback:

```text
uvicorn api:app --host 127.0.0.1 --port 8000
```

In another terminal, launch the UI:

```text
streamlit run ui.py
```

The UI talks to the API over HTTP. It defaults to
`http://127.0.0.1:8000`; set `FLOWPILOT_API_URL` if the local API uses another
address. OpenAI mode requires a separate billable-request confirmation in the
UI. The reviewer enters the API's configured `REVIEW_API_KEY` to approve or
reject a pending response; the key is entered in a password field in the
sidebar. The Evaluation and Review stats tabs use the same key to read
`GET /evaluation` and `GET /stats`. Set `EVALUATION_RESULTS_PATH` in the API
process to serve an evaluation report from a different path; by default it
reads `evals/results.json`. Review stats report category counts only if the
approval audit table contains a category column.

Keep both services on loopback for local demonstration. The generation API
and n8n webhook are not a production authentication boundary.

## Integrations and manual OpenAI verification

- Importable, inactive n8n export: [docs/n8n-integration.md](docs/n8n-integration.md)
- Manual-only live OpenAI procedure: [docs/openai-verification.md](docs/openai-verification.md)

Live OpenAI verification is **UNVERIFIED** and requires a user-provided key.
Automated checks never make paid OpenAI calls. Do not run the manual verifier
unless you intentionally want to send one billable request.

## Developer checks

```text
python -m unittest -v
python flowpilot.py --classify-tests
python scripts/evaluate.py
ruff check .
coverage run -m unittest discover -v
coverage report --fail-under=65
python scripts/check_secrets.py
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
[docs/TESTING.md](docs/TESTING.md), and
[docs/SECURITY.md](docs/SECURITY.md) for design, validation, and deployment
limitations.
