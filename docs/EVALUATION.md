# Offline Evaluation

## Run details

| Setting | Value |
|---|---|
| Mode | `template` |
| Cases | 71 |
| Evaluated (UTC) | 2026-11-04T04:12:14.545509Z |
| Git commit | `8363041` |

Measured on 71 synthetic cases in template/mock mode. Not live OpenAI. Not production data.

## Metrics

| Metric | Result |
|---|---:|
| Classification accuracy | 100.0% |
| Order-ID extraction accuracy | 100.0% |
| Outcome accuracy | 100.0% |
| Safety pass rate | 100.0% |
| Mean processing time | 33.121 ms |
| p95 processing time | 43.448 ms |

## Classification accuracy per tag

| Tag | Cases | Accuracy |
|---|---:|---:|
| ambiguous | 9 | 100.0% |
| clear | 35 | 100.0% |
| edge | 16 | 100.0% |
| injection | 4 | 100.0% |
| known_limitation | 3 | 100.0% |
| malformed_id | 4 | 100.0% |
| missing_id | 10 | 100.0% |
| nonexistent_order | 6 | 100.0% |
| typo | 3 | 100.0% |

## Confusion matrix

| Expected \\ Actual | Address Change | Cancellation | Damaged Item | Delivery Issue | General Inquiry | Payment Issue | Refund Inquiry | Unknown Request |
|---|---|---|---|---|---|---|---|---|
| Address Change | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| Cancellation | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 |
| Damaged Item | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 |
| Delivery Issue | 0 | 0 | 0 | 16 | 0 | 0 | 0 | 0 |
| General Inquiry | 0 | 0 | 0 | 0 | 5 | 0 | 0 | 0 |
| Payment Issue | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 |
| Refund Inquiry | 0 | 0 | 0 | 0 | 0 | 0 | 19 | 0 |
| Unknown Request | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 |

## Failed cases (0)

| Case | Expected | Actual | Safety |
|---|---|---|---|
| None | — | — | pass |
