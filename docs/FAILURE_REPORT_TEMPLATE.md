# Agent failure report

Use this template for an observed failure. Keep customer content fictional;
include only measured values and redact credentials.

## What I tried

- Date/time (UTC): `[fill from the run]`
- Mode and model: `[mock/live; model name from report]`
- Command or API workflow: `[exact non-secret command/request shape]`
- Evaluation case ID(s): `[case IDs]`
- Environment/configuration relevant to the behavior: `[non-secret settings]`

## What failed

- Expected behavior: `[what should have happened]`
- Actual behavior: `[what happened]`
- Failure reason code(s): `[e.g. WRONG_TOOL, HALLUCINATED_FACT]`
- Evidence: `[report path, test output, or run ID; do not paste credentials]`

## Why it failed

`[Root-cause analysis supported by the evidence. Distinguish confirmed facts
from hypotheses.]`

## The fix

- Changed files or behavior: `[list]`
- Regression test added/updated: `[test name]`
- Remaining risks or known limitations: `[list or “none observed”]`

## Before/after metrics

| Metric | Before | After | Source |
|---|---:|---:|---|
| Category accuracy | `[measured value or not measured]` | `[measured value or not measured]` | `[report path]` |
| Latency p50 / p95 | `[measured values or not measured]` | `[measured values or not measured]` | `[report path]` |
| Prompt/completion tokens | `[measured values or not measured]` | `[measured values or not measured]` | `[report path]` |
| Estimated cost | `[measured value or unavailable]` | `[measured value or unavailable]` | `[configured price table and report]` |

Do not substitute mock-mode values for live results or infer costs when pricing
is missing.
