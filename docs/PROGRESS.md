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
