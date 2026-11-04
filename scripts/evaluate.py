"""Run the labelled, offline FlowPilot evaluation."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import database  # noqa: E402
import flowpilot  # noqa: E402
from llm_response_generator import MockLLM  # noqa: E402

OUTCOMES = {
    "order_found",
    "order_not_found",
    "malformed_order_id",
    "missing_order_id",
    "no_order_needed",
}
TAGS = {
    "clear",
    "ambiguous",
    "typo",
    "missing_id",
    "malformed_id",
    "nonexistent_order",
    "injection",
    "edge",
    "known_limitation",
}
LOOKUP_CATEGORIES = {"Delivery Issue", "Refund Inquiry", "Payment Issue"}
REAL_CATEGORIES = {
    category for _, category, _ in flowpilot.RULES
} | {flowpilot.FALLBACK_CATEGORY}
REQUIRED_FIELDS = {
    "id",
    "message",
    "expected_category",
    "expected_order_id",
    "expected_outcome",
    "forbidden_phrases",
    "tags",
}

# Evaluation must stay offline even when a caller's shell contains a key.
os.environ.pop("OPENAI_API_KEY", None)


def _validate_cases(cases: list[dict[str, Any]]) -> None:
    seen_ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict) or not REQUIRED_FIELDS <= case.keys():
            raise ValueError(f"Case {index} is missing required fields.")
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id or case_id in seen_ids:
            raise ValueError(f"Case {index} has an invalid or duplicate id.")
        seen_ids.add(case_id)
        if not isinstance(case["message"], str):
            raise ValueError(f"Case {case_id} message must be a string.")
        if case["expected_category"] not in REAL_CATEGORIES:
            raise ValueError(f"Case {case_id} has an unknown category.")
        if case["expected_order_id"] is not None and not isinstance(
            case["expected_order_id"], str
        ):
            raise ValueError(f"Case {case_id} expected_order_id must be a string or null.")
        if case["expected_outcome"] not in OUTCOMES:
            raise ValueError(f"Case {case_id} has an unknown outcome.")
        if not isinstance(case["forbidden_phrases"], list) or not all(
            isinstance(phrase, str) for phrase in case["forbidden_phrases"]
        ):
            raise ValueError(f"Case {case_id} forbidden_phrases must be a string list.")
        if not isinstance(case["tags"], list) or not all(
            isinstance(tag, str) and tag in TAGS for tag in case["tags"]
        ):
            raise ValueError(f"Case {case_id} has invalid tags.")


def _provider_for_mode(mode: str):
    if mode == "template":
        return None
    if mode == "mock":
        response = "I can help with your request."
        return MockLLM({category: response for category in REAL_CATEGORIES})
    raise ValueError("mode must be 'template' or 'mock'.")


def _actual_outcome(result: dict[str, Any]) -> str:
    if result["category"] not in LOOKUP_CATEGORIES:
        return "no_order_needed"
    if result.get("input_error") == "database_unavailable":
        return "database_error"
    if result.get("input_error") == "malformed_order_id":
        return "malformed_order_id"
    if result.get("order_id") is None:
        return "missing_order_id"

    details_key = {
        "Delivery Issue": "order_details",
        "Refund Inquiry": "refund_details",
        "Payment Issue": "payment_details",
    }[result["category"]]
    details = result.get(details_key)
    if isinstance(details, dict):
        return "order_found"
    if details == "Order not found in sample database":
        return "order_not_found"
    return "workflow_error"


def evaluate_cases(cases: list[dict[str, Any]], mode: str = "template") -> dict[str, Any]:
    """Evaluate cases in a fresh temporary database and return metrics."""
    if mode not in {"template", "mock"}:
        raise ValueError("mode must be 'template' or 'mock'.")
    if not cases:
        raise ValueError("At least one evaluation case is required.")
    _validate_cases(cases)
    os.environ.pop("OPENAI_API_KEY", None)

    original_database_path = database.DATABASE_PATH
    case_results: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="flowpilot-eval-") as directory:
            database.DATABASE_PATH = Path(directory) / "orders.db"
            provider = _provider_for_mode(mode)
            for case in cases:
                started = time.perf_counter()
                result = flowpilot.run_workflow_with_provider(case["message"], provider)
                elapsed_ms = (time.perf_counter() - started) * 1000
                if (
                    result.get("approval_status") != "Pending"
                    or result.get("approval") is not None
                    or result.get("can_proceed") is not False
                ):
                    raise RuntimeError("Evaluation workflow violated pending human approval.")

                actual = {
                    "category": result["category"],
                    "order_id": result["order_id"],
                    "outcome": _actual_outcome(result),
                }
                expected = {
                    "category": case["expected_category"],
                    "order_id": case["expected_order_id"],
                    "outcome": case["expected_outcome"],
                }
                response = result.get("customer_response") or ""
                safety_pass = not any(
                    phrase.casefold() in response.casefold()
                    for phrase in case["forbidden_phrases"]
                )
                case_results.append(
                    {
                        "id": case["id"],
                        "expected": expected,
                        "actual": actual,
                        "safety_pass": safety_pass,
                        "processing_time_ms": elapsed_ms,
                    }
                )
    finally:
        database.DATABASE_PATH = original_database_path

    total = len(case_results)
    classification_correct = sum(
        item["expected"]["category"] == item["actual"]["category"]
        for item in case_results
    )
    order_id_correct = sum(
        item["expected"]["order_id"] == item["actual"]["order_id"]
        for item in case_results
    )
    outcome_correct = sum(
        item["expected"]["outcome"] == item["actual"]["outcome"]
        for item in case_results
    )
    safety_passes = sum(item["safety_pass"] for item in case_results)

    categories = sorted(REAL_CATEGORIES)
    confusion_matrix = {
        expected: {actual: 0 for actual in categories} for expected in categories
    }
    per_tag: dict[str, dict[str, int]] = {}
    cases_by_id = {case["id"]: case for case in cases}
    for item in case_results:
        expected_category = item["expected"]["category"]
        actual_category = item["actual"]["category"]
        confusion_matrix[expected_category][actual_category] += 1
        for tag in cases_by_id[item["id"]]["tags"]:
            stats = per_tag.setdefault(tag, {"count": 0, "correct": 0})
            stats["count"] += 1
            stats["correct"] += expected_category == actual_category

    timings = sorted(item["processing_time_ms"] for item in case_results)
    p95_index = max(0, math.ceil(0.95 * total) - 1)
    failures = [
        {
            "id": item["id"],
            "expected": item["expected"],
            "actual": item["actual"],
            "safety_pass": item["safety_pass"],
        }
        for item in case_results
        if (
            item["expected"] != item["actual"]
            or not item["safety_pass"]
        )
    ]
    return {
        "metrics": {
            "classification_accuracy": classification_correct / total,
            "order_id_extraction_accuracy": order_id_correct / total,
            "outcome_accuracy": outcome_correct / total,
            "safety_pass_rate": safety_passes / total,
            "mean_processing_time_ms": round(sum(timings) / total, 3),
            "p95_processing_time_ms": round(timings[p95_index], 3),
            "accuracy_per_tag": {
                tag: {
                    "count": stats["count"],
                    "classification_accuracy": stats["correct"] / stats["count"],
                }
                for tag, stats in sorted(per_tag.items())
            },
            "confusion_matrix": confusion_matrix,
        },
        "failures": failures,
    }


def _short_git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_report(
    results: dict[str, Any], result_path: Path, report_path: Path
) -> None:
    result_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    metrics = results["metrics"]
    lines = [
        "# Offline Evaluation",
        "",
        "## Run details",
        "",
        "| Setting | Value |",
        "|---|---|",
        f"| Mode | `{results['mode']}` |",
        f"| Cases | {results['case_count']} |",
        f"| Evaluated (UTC) | {results['timestamp_utc']} |",
        f"| Git commit | `{results['git_commit']}` |",
        "",
        f"Measured on {results['case_count']} synthetic cases in template/mock mode. Not live OpenAI. Not production data.",
        "",
        "## Metrics",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Classification accuracy | {metrics['classification_accuracy']:.1%} |",
        f"| Order-ID extraction accuracy | {metrics['order_id_extraction_accuracy']:.1%} |",
        f"| Outcome accuracy | {metrics['outcome_accuracy']:.1%} |",
        f"| Safety pass rate | {metrics['safety_pass_rate']:.1%} |",
        f"| Mean processing time | {metrics['mean_processing_time_ms']:.3f} ms |",
        f"| p95 processing time | {metrics['p95_processing_time_ms']:.3f} ms |",
        "",
        "## Classification accuracy per tag",
        "",
        "| Tag | Cases | Accuracy |",
        "|---|---:|---:|",
    ]
    for tag, stats in metrics["accuracy_per_tag"].items():
        lines.append(
            f"| {tag} | {stats['count']} | {stats['classification_accuracy']:.1%} |"
        )

    categories = sorted(metrics["confusion_matrix"])
    lines.extend(
        [
            "",
            "## Confusion matrix",
            "",
            "| Expected \\\\ Actual | "
            + " | ".join(categories)
            + " |",
            "|" + "---|" * (len(categories) + 1),
        ]
    )
    for expected in categories:
        values = metrics["confusion_matrix"][expected]
        lines.append(
            f"| {expected} | " + " | ".join(str(values[category]) for category in categories) + " |"
        )

    lines.extend(
        [
            "",
            f"## Failed cases ({len(results['failures'])})",
            "",
            "| Case | Expected | Actual | Safety |",
            "|---|---|---|---|",
        ]
    )
    if results["failures"]:
        for failure in results["failures"]:
            lines.append(
                f"| {failure['id']} | `{json.dumps(failure['expected'], ensure_ascii=False)}` "
                f"| `{json.dumps(failure['actual'], ensure_ascii=False)}` "
                f"| {'pass' if failure['safety_pass'] else 'FAIL'} |"
            )
    else:
        lines.append("| None | — | — | pass |")
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fail_under(value: str) -> float:
    try:
        threshold = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number from 0.0 to 1.0") from error
    if not 0.0 <= threshold <= 1.0:
        raise argparse.ArgumentTypeError("must be a number from 0.0 to 1.0")
    return threshold


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("template", "mock"), default="template")
    parser.add_argument("--fail-under", type=_fail_under)
    options = parser.parse_args(argv)

    cases_path = ROOT / "evals" / "cases.json"
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    evaluated = evaluate_cases(cases, mode=options.mode)
    results = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "git_commit": _short_git_commit(),
        "mode": options.mode,
        "case_count": len(cases),
        **evaluated,
    }
    _write_report(results, ROOT / "evals" / "results.json", ROOT / "docs" / "EVALUATION.md")

    metrics = results["metrics"]
    print(f"Mode: {options.mode}")
    print(f"Cases: {results['case_count']}")
    print(f"Classification accuracy: {metrics['classification_accuracy']:.1%}")
    print(f"Order-ID extraction accuracy: {metrics['order_id_extraction_accuracy']:.1%}")
    print(f"Outcome accuracy: {metrics['outcome_accuracy']:.1%}")
    print(f"Safety pass rate: {metrics['safety_pass_rate']:.1%}")
    print(f"Mean processing time: {metrics['mean_processing_time_ms']:.3f} ms")
    print(f"p95 processing time: {metrics['p95_processing_time_ms']:.3f} ms")
    print(f"Failed cases: {len(results['failures'])}")
    print(f"Git commit: {results['git_commit']}")
    print("Saved evals/results.json and docs/EVALUATION.md")
    if (
        options.fail_under is not None
        and metrics["classification_accuracy"] < options.fail_under
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
