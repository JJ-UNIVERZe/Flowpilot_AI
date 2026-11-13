# Testing and validation

## Local checks

Install the declared runtime and development dependencies:

```text
python -m pip install -r requirements-dev.txt
```

Run the complete offline suite and original classifier checks:

```text
python -m unittest discover -v
python flowpilot.py --classify-tests
```

The original labeled workflow evaluation and the separate agent evaluation are
both offline by default:

```text
python scripts/evaluate.py --mode mock
python run_eval.py --mode mock
```

`run_eval.py` writes `reports/eval_report.json` and
`reports/eval_report.md`. It evaluates the checked-in JSONL cases using a
temporary SQLite database and `MockLLM`. Set `--limit N` for a smaller subset,
`--output PATH` to choose another output folder,
`--slow-threshold-ms MS` to change the latency failure limit, and
`--fail-under 0.0-1.0` to configure the accuracy gate.

Live evaluation is a user-authorized operation only:

```text
python run_eval.py --mode live --live --limit 5
```

It requires `OPENAI_API_KEY`; with no explicit `--limit`, live mode defaults to
five cases. `LLM_MODE=live` is an alternative explicit authorization. The
agent API additionally requires `LLM_MODE=live` in its process environment
and a per-request `mode: "live"`. Never include a real key in commands,
reports, or source files. Automated tests and CI do not call the live provider.

## Lint, coverage, and secret hygiene

```text
ruff check .
coverage run -m unittest discover -v
coverage report --fail-under=65
python scripts/check_secrets.py
```

CI runs the existing unittest, lint, coverage, classification, and secret
checks, plus the mock agent evaluation. `FLOWPILOT_EVAL_MIN_ACCURACY` controls
the evaluation threshold in CI; the workflow default is `0.80`.

## Agent test coverage

The offline suite covers strict tool arguments and errors, customer ownership,
refund policy rules, tool-call audit rows, scripted mock tool calls, graph
routing, hallucination/promise validation, approval pause/edit/resume/reject,
metrics persistence and aggregation, each reason-coded evaluator, report
comparison, authenticated API routes, and the Streamlit dashboard.

`evals/agent_cases.jsonl` extends the existing workflow examples with
nonexistent and cross-customer orders, expired and already-requested refunds,
missing IDs, vague requests, Malayalam-style transliteration, prompt injection,
an unauthorized discount request, and a long input. It is a small synthetic
test set, not a representative production benchmark.

## Verification boundaries

- Unit tests use temporary SQLite files and the mock provider. No live OpenAI
  request is made.
- Mock-mode token usage and cost are zero by definition. They do not measure
  model tokenization or real provider quality.
- Live OpenAI behavior and pricing remain unverified until the user runs an
  authorized small-limit evaluation with a configured key and price table.
- LangGraph approval checkpoints and pending run payloads use in-memory state;
  process restarts discard pending work.
- The GitHub Actions workflow is configured but hosted CI results depend on a
  repository run.
