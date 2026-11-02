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
