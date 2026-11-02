# Testing and validation

## Local checks

Install the declared development dependencies:

```text
python -m pip install -r requirements-dev.txt
```

Run the full suite and the independent classifier checks:

```text
python -m unittest -v
python flowpilot.py --classify-tests
```

Lint, coverage, and secret hygiene:

```text
ruff check .
coverage run -m unittest discover -v
coverage report --fail-under=65
python scripts/check_secrets.py
```

All automated tests use temporary SQLite databases or mocked providers. They do
not make OpenAI requests. The live verifier is intentionally not part of test
discovery.

## Current evidence

- Full unittest suite: **212 passed**.
- Built-in classification CLI: **25/25 passed**.
- Ruff correctness-focused checks: passed.
- Branch coverage: **76%** across current application modules; CI enforces 65%.
- Clean Python 3.13 environment installation from `requirements-dev.txt`: passed.
- Secret scan over the worktree and available Git history: passed.
- GitHub Actions workflow is configured for Python 3.11 and 3.13, but a hosted
  run has not occurred because no remote is configured.

The 184-test baseline at commit `5968219` measured 72% coverage on the shared
core modules. The same shared modules now measure 77%. Coverage is a useful
regression signal, not proof that every behavior or deployment path is safe.

## Important test boundaries

- API route tests use FastAPI's in-process test client and temporary databases.
- Streamlit rendering is checked with AppTest; the HTTP client is tested with
  mocked HTTP requests. A real browser-to-server session has not been run.
- The n8n file is checked as JSON and for safe routing, but n8n runtime import
  is unverified.
- OpenAI client behavior is mocked. Live OpenAI verification remains
  unverified and is a user action described in
  [openai-verification.md](openai-verification.md).
