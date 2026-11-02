# FlowPilot AI autonomous phase log

## Phase 0 — Baseline audit

**Status: Complete**

- Inspected all existing application modules, tests, dependency/configuration files, and generated JSON without reading a `.env` file.
- Root tree contained the six Python modules, `requirements.txt`, `.gitignore`, `.env.example`, `orders.db`, and `workflow_result.json`; no README, API/UI/integration directories, docs, scripts, or CI files existed.
- `.git` and `.env` were absent.
- Verified the Stage 10.2 refund-approval rejection and actual template fallback with direct probes using a temporary SQLite database and mocked providers.
- Limited source/config secret-pattern scan: 0 matches. Git history scan is unavailable because the project had no Git repository.
- Baseline command `python -m unittest -v`: **184 tests passed** (`Ran 184 tests in 5.734s`, exit 0).
- Baseline command `python flowpilot.py --classify-tests`: **25/25 passed**, exit 0.
- No OpenAI call was made.

| Area | Status | Evidence / gap |
|---|---|---|
| Core workflow and classification | IMPLEMENTED / TESTED | 184 unit tests and 25 CLI classification checks passed |
| SQLite lookups | IMPLEMENTED / TESTED | Isolated test databases; setup currently seeds/updates sample rows during workflow initialization |
| Provider abstraction and OpenAI SDK | IMPLEMENTED / TESTED | Mocked provider tests; live service remains unverified |
| Factual validation | IMPLEMENTED / TESTED | Stage 10.2 regressions pass; regex-based checks are not a semantic guarantee |
| Human approval and audit log | IMPLEMENTED / TESTED | Approval-state, audit and export tests pass |
| JSON export | IMPLEMENTED / TESTED | Existing export tests pass; no schema version |
| API, UI, n8n, CI, user docs | NOT IMPLEMENTED | No corresponding files or directories existed at baseline |
| Git hygiene/history | PARTIALLY VERIFIED | Ignore rules present; Git/history was absent |

### Baseline risks

1. The setup path calls database initialization that reseeds/updates fictional records; later phases will separate safe initialization from lookup semantics without mutating customer order data.
2. The CLI has template and OpenAI paths but no explicit mock mode.
3. Pattern validation cannot guarantee recognition of all natural-language facts.
4. No repository history exists to audit. No potential secret literals were identified in the inspected source/config files.
5. Dependencies are not pinned, and live OpenAI verification has not happened.

**Next:** Phase 1, repository hygiene and secret safety. No Phase 0 files were modified before baseline tests.

## Phase 1 — Repository hygiene and secret safety

**Status: Complete**

- Initialized Git because the workspace had no repository; no existing history or remote was changed.
- Extended `.gitignore` for pytest caches, SQLite variants, exports/logs, and common IDE directories. `.env` and `.env.*` remain ignored; `.env.example` is explicitly allowed.
- Expanded `.env.example` using empty/placeholding configuration values only. No `.env` was created.
- Added `scripts/check_secrets.py`. It checks tracked/untracked project text and available Git history; it suppresses matching values and skips environment files, virtual environments, databases, and generated binaries.
- `python scripts/check_secrets.py`: passed, no likely secret patterns found. Since Git was newly initialized, the pre-commit history was empty.
- `git status --short`: returned expected untracked source/docs/scripts. `.venv`, `orders.db`, and generated `workflow_result.json` were excluded.
- Existing application verification remains at baseline: 184 unit tests and 25/25 classification checks passed before the hygiene-only edits. Re-run before commit.
- No remote was configured or used.

**Next:** Phase 2, core workflow hardening and CLI provider modes.

## Phase 2 — Core workflow hardening and provider modes

**Status: Complete**

- Changed database initialization so existing sample order, refund, and payment values are not overwritten on later lookups. A regression test edits an existing record, initializes again, and verifies that the stored values survive.
- Updated `MockLLM` to read the structured request category from customer-context content rather than being misled by matching words in system instructions.
- Added explicit CLI modes for template, mock, and OpenAI providers, with `--message`, `--db`, and `--json` options. Positional messages and the existing `--llm` flag remain supported.
- Added JSON schema version `1.0` to workflow results and exported JSON.
- Added CLI routing, missing-key safety, mock provider, data-preservation, and JSON-version regression coverage. The complete suite passes: `python -m unittest -v` — **191 tests passed** (`Ran 191 tests in 6.198s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.
- No OpenAI API request was made. OpenAI CLI verification used the missing-key path, and provider workflows were mocked.

**Next:** Phase 3, add manual-only live OpenAI verification guidance and document that live service behavior remains unverified.

## Phase 3 — Manual OpenAI verification

**Status: Complete; live service verification remains UNVERIFIED**

- Added `scripts/verify_openai_live.py`. It requires an explicit `--confirm-live-call`, refuses to proceed without `OPENAI_API_KEY`, sends only a fixed non-customer prompt, and does not print the credential or response body.
- Added `docs/openai-verification.md` with the manual procedure, billable-request warning, and limitations of the check.
- Parsed the new script for syntax without executing it. The live verifier was **not run** and no real OpenAI request was made.
- `python -m unittest -v` — **191 tests passed** (`Ran 191 tests in 6.418s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.

**Next:** Phase 4, add a thin FastAPI interface and isolated tests if dependencies and current application boundaries support it.

## Phase 4 — Thin FastAPI interface

**Status: Complete**

- Added FastAPI endpoints for health checks, response generation, and human review. The workflow endpoint only creates a Pending result; it never approves or executes an action.
- Added template, mock, and OpenAI provider selection. OpenAI mode without a configured key uses the existing template fallback.
- Protected review decisions with `REVIEW_API_KEY`, constant-time key comparison, strict Approved/Rejected input validation, and the existing SQLite audit log. Invalid or unauthorized review requests do not change pending state.
- Pending responses live in process memory until reviewed. A process restart discards unresolved pending items; completed review decisions remain in SQLite.
- Added FastAPI, Uvicorn, and HTTPX dependencies. Installed the declared API dependencies in the workspace Python environment.
- Added eight API tests using FastAPI's in-process test client and temporary SQLite databases. `python -m unittest -v` — **199 tests passed** (`Ran 199 tests in 6.087s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.
- No OpenAI API request was made.

**Next:** Phase 5, strengthen database and audit integrity and cover failure/replay cases.

## Phase 5 — Database and audit integrity

**Status: Complete**

- Added SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers for `approval_logs`, enforcing append-only decisions at the database layer for normal application operations.
- Added regressions proving the audit row remains unchanged after attempted update/delete and old databases still receive the required audit structures.
- Added a row snapshot test proving order, refund, and payment lookups do not write existing facts.
- Existing audit records and request outcomes remain persisted in SQLite; pending API workflows remain process-local until a decision is recorded.
- `python -m unittest -v` — **201 tests passed** (`Ran 201 tests in 7.178s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.
- No OpenAI API request was made.

**Next:** Phase 6, add a Streamlit interface that uses the HTTP API rather than duplicating workflow or approval logic.

## Phase 6 — Streamlit client

**Status: Complete**

- Added `ui.py`, a Streamlit interface that sends generation and review requests only through the FastAPI endpoints.
- The UI displays generated responses as not yet approved, requires the reviewer key before a decision can be sent, and exposes separate Approve/Reject actions.
- OpenAI mode is disabled until the reviewer explicitly confirms that the request may be billable.
- Added four mocked HTTP-client tests and an AppTest rendering/consent test. `python -m unittest -v` — **206 tests passed** (`Ran 206 tests in 7.584s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.
- No OpenAI API request was made. No browser-based end-to-end session was run; UI behavior is covered in-process.

**Next:** Phase 7, add a credential-free n8n workflow export that calls the API without bypassing human approval.

## Phase 7 — n8n integration

**Status: Complete; n8n runtime import remains UNVERIFIED**

- Added an inactive n8n workflow export with a webhook, a single template-mode API request, and a response node.
- The export contains no credentials, and it does not call approval or any refund/payment/order mutation endpoint.
- Added static tests for JSON validity, inactive status, credential absence, API routing, and the Pending-only path.
- Confirmed all n8n graph connection references point to exported nodes. n8n is not installed here, so import/runtime behavior was not tested.
- `python -m unittest -v` — **209 tests passed** (`Ran 209 tests in 7.332s`, exit 0).
- `python flowpilot.py --classify-tests` — **25/25 passed**, exit 0.
- `python scripts/check_secrets.py` — passed; no likely secret patterns found.
- No OpenAI API request was made.

**Next:** Phase 8, add lint/coverage/CI and security regression checks without weakening existing tests.
