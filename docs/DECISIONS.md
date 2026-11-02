# FlowPilot AI implementation decisions

## Phase 0

- Keep the existing single-file unittest layout and established public workflow functions unless a concrete compatibility issue requires otherwise. The 184-test baseline passes.
- Do not inspect `.env` contents; the file did not exist. `.env.example` contains placeholders.
- Git history cannot be audited before a repository exists. The baseline source/config scan found no likely secret literals and did not reveal matching text.
- Do not run live OpenAI verification. Automated checks must remain isolated with mock clients and temporary databases.
- Treat factual validation as a useful safety filter, not a proof of semantic correctness; retain mandatory human review.

## Phase 1

Decisions will be recorded here as repository hygiene and secret-check implementation proceeds.

## Phase 2

- Keep the existing sample database records as initialization fixtures, but only insert missing rows; initialization must not rewrite financial/order state on every workflow run.
- Add `--mode template|mock|openai` as the explicit provider selector while preserving the positional-message and `--llm` compatibility paths.
- Use a small JSON schema version (`1.0`) in both the workflow result and export so consumers can identify the output shape.
- Keep provider tests offline. OpenAI mode without a key exits with a clear error; no API call is made.
- Add focused CLI and workflow regression cases without replacing existing tests. The full suite now has 191 passing tests, up from the 184-test baseline.

## Phase 3

- Put live-provider verification behind a separate manual script instead of adding it to unit tests or application startup.
- Require an explicit `--confirm-live-call` flag and use a fixed prompt containing no customer or order details. This reduces accidental requests and prevents test data from leaving the machine.
- Keep live verification marked UNVERIFIED until the user supplies a key and chooses to run it. Do not execute the verifier during development.

## Phase 4

- Keep the HTTP layer thin: delegate lookup and response generation to the established workflow function and delegate review persistence to `approval.record_approval`.
- Bind the API to loopback for local use. Require a separately configured `REVIEW_API_KEY` before allowing any review decision; a missing server-side key fails closed.
- Keep unresolved API responses in memory and make that limitation explicit rather than introducing a new persistence schema. Audit only explicit decisions, using the existing approval table.
- Use a temporary database and in-process TestClient for API coverage. Tests verify Pending default, auth failure, approval/rejection audit, invalid-decision handling, and offline fallback.

## Phase 5

- Enforce audit immutability with SQLite triggers so update/delete protection does not depend only on callers following a convention.
- Keep order/refund/payment query functions read-only for existing records. Initialization remains responsible for creating/migrating local fixture storage when absent.
- These controls protect against ordinary application operations, not a privileged operator who can alter the database schema or remove triggers.

## Phase 6

- Implement the UI as an HTTP client instead of importing the workflow or database modules. This keeps one source of truth for classification, lookup, fallback, and audit behavior.
- Do not embed reviewer credentials or API keys. Reviewers enter the review key into a password-style input, and the UI forwards it only in the review header.
- Require a separate checkbox before OpenAI mode can make a potentially billable request. Keep the default mode local/template-based.

## Phase 7

- Export an inactive n8n example that uses template mode and calls only the local generation API. It returns a Pending result to its caller; it does not approve or transmit customer responses.
- Leave credentials out of the workflow export entirely. The webhook is for local demonstration only and must receive deployment-appropriate authentication/network controls before exposure.
- Mark n8n import and runtime behavior unverified because no n8n instance is installed in this environment.

## Phase 8

- Keep lint scope to Ruff's correctness-focused `E4`, `E7`, `E9`, and `F` rules to avoid a broad style rewrite of the legacy project.
- Enforce a 65% coverage floor: the measured current application coverage is 76%, and the baseline comparison shows shared-module coverage increasing from 72% to 77%.
- Preserve a clean install command in `requirements-dev.txt` so CI and developers use the same declared dependency set. Fix malformed requirement syntax rather than bypassing dependency installation.
- Type-only imports need postponed annotation evaluation when the runtime package may be unavailable; verify the fallback path in a clean environment before accepting that pattern.
- CI is configured but its hosted run remains unverified until the repository is connected to a remote.

## Phase 9

- Document the actual behavior and known limitations rather than promising production readiness. In particular, `.env` is not loaded automatically, API pending records are process-local, and OpenAI/n8n/hosted-CI checks remain unverified.
- Leave `OPENAI_MODEL` blank in the example and make an empty setting fall back to the provider's documented default model. This keeps the example credential-free and avoids passing an empty model to the SDK when a file-based environment loader is used externally.
- Final coverage comparison uses the unchanged shared core modules to avoid making the expanded module set look like a direct one-to-one baseline.
