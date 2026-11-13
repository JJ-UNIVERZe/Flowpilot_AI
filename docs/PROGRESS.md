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

## Phase 8 — Lint, coverage, CI, and security checks

**Status: Complete**

- Added Ruff and Coverage development requirements, Ruff/Coverage configuration, `.coverage` ignores, and a GitHub Actions workflow for Python 3.11 and 3.13.
- Added CI steps for lint, branch coverage, unit tests, classification checks, and repository secret scanning. The coverage gate is 65%.
- The first clean-install attempt exposed a malformed `requirements.txt` comment; corrected it. The clean install then exposed a runtime annotation name error after introducing type-only imports; adding postponed annotation evaluation fixed it.
- Removed unused imports/assignments and an unreachable duplicate fallback block. These changes do not alter response or approval behavior.
- Clean Python 3.13 virtual environment installed `requirements-dev.txt` successfully. In that environment: Ruff passed; `coverage run -m unittest -v` passed **211 tests** (`Ran 211 tests in 12.729s`); `coverage report --fail-under=65` passed at **76%**; classification passed **25/25**; secret scan passed.
- Measured baseline commit `5968219` with the original 184 tests: shared application modules had **72%** coverage. The same shared modules now have **77%** coverage; all current application modules together have **76%**.
- Pylance workspace diagnostics now show no type/error-level diagnostics. Remaining editor diagnostics are unused-symbol warnings in legacy helpers/tests.
- No OpenAI API request was made. GitHub-hosted CI itself has not run because no remote is configured.

**Next:** Phase 9, write the user-facing project, architecture, testing, and security documentation; then perform the final audit.

## Phase 9 — Documentation and final audit

**Status: Complete for local implementation; external verifications remain pending**

- Added the project README plus architecture, testing, and security documentation. The docs describe CLI/API/UI usage, provider data flow, human approval, secret handling, audit limits, and deployment boundaries.
- Simplified `.env.example` to blank credentials and optional model configuration; clarified that the application reads process environment variables and does not load `.env` itself.
- Hardened OpenAI model configuration so an empty `OPENAI_MODEL` uses the documented `gpt-4o-mini` default. Added an offline regression test using a mocked SDK constructor.
- Final `python -m unittest -v` — **212 tests passed** (`Ran 212 tests in 11.370s`, exit 0). A fresh Python 3.13 environment also passed all **212 tests** (`Ran 212 tests in 7.437s`).
- Final classification CLI — **25/25 passed**.
- Final Ruff checks — passed. Branch coverage — **76%**; the configured 65% threshold passed.
- Final secret scan — passed; `.env` is ignored and not tracked, while `.env.example` is tracked.
- Import-time dependency graph has no cycles; the lazy template fallback is covered by the existing import/reload regression test. The LLM workflow performs one lookup, covered by its no-duplicate-lookup regression test.
- No OpenAI API call was made.
- Hosted GitHub Actions, browser-to-server networking, n8n import/runtime, and live OpenAI verification remain unverified. Live OpenAI requires user-provided credentials and is explicitly not part of automated execution.

### Final project status

Phases 0–9 have been implemented and validated locally. This is a functioning local demo, not a production-ready customer-support service. The unresolved external/infrastructure checks and security limitations are documented in `docs/SECURITY.md` and the final response.

## Upgrade request baseline architecture (2026-11-13)

Before starting the requested LLM-agent phases, the current repository contains:

- `flowpilot.py`: deterministic rule-based classification, order-ID extraction, one relevant SQLite lookup, response coordination, factual validation handoff, CLI, and human approval.
- `database.py` and `orders.db`: fictional sample orders, refund/payment facts, additive schema migration, and append-only approval audit records. Existing fixtures are not overwritten on initialization.
- `response_generator.py`, `llm_response_generator.py`, and `knowledge_base.py`: safe template responses, optional official OpenAI SDK provider plus offline `MockLLM`, local policy retrieval, and response validation/fallback.
- `api.py`: FastAPI health, workflow/process, policy search, evaluation, statistics, and authenticated review endpoints. New workflows remain pending; pending workflow state is process-local.
- `ui.py`: Streamlit client for the API, generation, reviewer decisions, evaluation, and review statistics.
- `scripts/evaluate.py` and `evals/{cases.json,baseline.json,results.json}`: existing offline labeled workflow evaluation and its recorded result.
- `test_*.py`, `requirements*.txt`, `pyproject.toml`, `.github/workflows/ci.yml`, and `scripts/check_secrets.py`: unittest coverage, runtime/dev dependencies, Ruff/Coverage configuration, CI, and secret scanning.
- `README.md` and `docs/`: setup, architecture, security, testing, policy, evaluation, and manual live-provider guidance.

The requested extension should therefore add isolated tool-calling and agent-run capabilities while preserving the original endpoints, classifier, response generation, and review behavior. The prior final audit records 212 tests, Ruff, coverage, classifier, and secret scan as passing; these are historical claims and will be re-run against this workspace before relying on them.

## Requested agent upgrade

**Baseline:** Architecture recorded above before implementation.

### Phase 1 — Tool calling

**Status: Complete**

- Added additive SQLite migration for fictional customer IDs, items, amounts, and order dates; added a fictional seventh seed order, a `refunds` table, and a `tool_call_logs` table without changing the existing lookup API.
- Added `tools.py` with strictly validated, structured `lookup_order` and policy-based `get_refund_info` functions. Cross-customer lookups do not reveal order data; the refund window and excluded categories are environment-configurable.
- Added strict OpenAI function schemas, structured tool-dispatch validation, and persisted tool name, arguments, result status, and duration for each invocation.
- Extended `OpenAIProvider` and `MockLLM` with tool-enabled responses and a scriptable mock tool-call sequence while preserving existing text-generation behavior.
- Added blank environment placeholders and policy settings; no key was added and no live call was made.
- Validation: `python -m unittest discover -v` — **262 passed**; `ruff check .` — passed; `python scripts/check_secrets.py` — passed.

**Remaining:** Phases 2–5 (LangGraph and reviewer approval, run metrics, automated agent evaluation, API/UI integration, and documentation).

### Phase 2 — LangGraph workflow and human approval

**Status: Complete**

- Added typed `agent_graph.py`: reuses the established classifier, enables bounded LLM tool calls, requires an ownership lookup before refund details, validates order facts/amounts/promises, and uses a safe fallback on unsupported drafts.
- Added a LangGraph `interrupt` approval checkpoint with in-memory checkpointing, editable drafts that are revalidated, and explicit approve/reject outcomes. No customer message is sent and no order/refund data is mutated.
- Added key-protected agent-run retrieval and approve/reject endpoints while preserving the original API routes. Review decisions are written through the existing immutable approval audit.
- Added a Streamlit Agent Review tab for the draft, tool results, validation flags, metrics, and approve/edit/reject actions. Live calls require explicit selection and a configured OpenAI key; mock is default.
- Validation: `python -m unittest discover -v` — **272 passed**; `ruff check .` — passed; `python scripts/check_secrets.py` — passed. No live OpenAI request was made.

**Remaining:** Phases 3–5 (persisted metrics and summary, expanded automated evaluation/CI threshold, final tests and documentation).

### Phase 3 — Latency and token-cost tracking

**Status: Complete**

- Added `metrics.py` and an additive SQLite `run_metrics` table with run mode/model, total and per-node latency, LLM latency, token counts, tool-call count, and estimated USD cost.
- Agent provider responses now expose token usage/model metadata. Mock mode records zero tokens and zero cost; live cost remains unavailable unless both per-million-token prices are configured via environment variables.
- Added authenticated `GET /metrics/summary` with count, average, p50/p95 latency, token totals, and priced-run count; added a Streamlit Agent Metrics dashboard page.
- Validation: `python -m unittest discover -v` — **276 passed**; `ruff check .` — passed; `python scripts/check_secrets.py` — passed. No live OpenAI request was made.

**Remaining:** Phases 4–5 (60+ agent evaluation cases, reason-coded evaluators, mock/live evaluation CLI, comparison and CI gate, final tests/docs).
