"""Run the offline or explicitly authorized live agent evaluation."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import agent_graph  # noqa: E402
import database  # noqa: E402
from evals.evaluators import evaluate_case  # noqa: E402
from llm_response_generator import MockLLM, OpenAIProvider  # noqa: E402


DEFAULT_DATASET = ROOT / "evals" / "agent_cases.jsonl"
DEFAULT_SLOW_THRESHOLD_MS = 5000.0
AGENT_CATEGORIES = {
    "Delivery Issue",
    "Refund Inquiry",
    "Payment Issue",
    "General Inquiry",
    "Damaged Item",
    "Cancellation",
    "Address Change",
    "Unknown Request",
}


def _read_dataset(path: Path) -> list[dict[str, Any]]:
    cases = []
    seen: set[str] = set()
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Invalid JSONL at line {line_number}.") from error
        required = {
            "id",
            "message",
            "customer_id",
            "expected_category",
            "expected_tools",
            "must_escalate",
            "tags",
        }
        if not isinstance(case, dict) or not required <= case.keys():
            raise ValueError(f"Evaluation case on line {line_number} is incomplete.")
        if case["id"] in seen or not isinstance(case["id"], str):
            raise ValueError(f"Evaluation case on line {line_number} has a duplicate ID.")
        if case["expected_category"] not in AGENT_CATEGORIES:
            raise ValueError(f"Evaluation case {case['id']} has an unknown category.")
        if not isinstance(case["message"], str) or not isinstance(case["expected_tools"], list):
            raise ValueError(f"Evaluation case {case['id']} has invalid field types.")
        seen.add(case["id"])
        cases.append(case)
    if len(cases) < 60:
        raise ValueError("The agent evaluation dataset must contain at least 60 cases.")
    return cases


def _provider(mode: str, live_authorized: bool = False):
    if mode == "mock":
        reply = "I can help check your request. A human reviewer will verify the details."
        return MockLLM({category: reply for category in AGENT_CATEGORIES})
    if not (live_authorized or os.getenv("LLM_MODE") == "live"):
        raise ValueError("Live evaluation requires --live or LLM_MODE=live.")
    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("Live evaluation requires OPENAI_API_KEY.")
    return OpenAIProvider()


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def evaluate_cases(
    cases: list[dict[str, Any]],
    mode: str = "mock",
    slow_threshold_ms: float = DEFAULT_SLOW_THRESHOLD_MS,
    live_authorized: bool = False,
) -> dict[str, Any]:
    """Run each case against an isolated temporary database and agent graph."""
    if mode not in {"mock", "live"}:
        raise ValueError("mode must be 'mock' or 'live'.")
    if not cases:
        raise ValueError("At least one evaluation case is required.")
    original_database_path = database.DATABASE_PATH
    provider = _provider(mode, live_authorized)
    reference_date = os.getenv("FLOWPILOT_EVAL_REFERENCE_DATE", "2026-11-13")
    try:
        date.fromisoformat(reference_date)
    except ValueError as error:
        raise ValueError("FLOWPILOT_EVAL_REFERENCE_DATE must use YYYY-MM-DD.") from error
    previous_as_of_date = os.environ.get("REFUND_AS_OF_DATE")
    os.environ["REFUND_AS_OF_DATE"] = reference_date
    outputs: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="flowpilot-agent-eval-") as directory:
            database.DATABASE_PATH = Path(directory) / "orders.db"
            database.create_database()
            for case in cases:
                run_id = f"eval-{case['id']}"
                graph = agent_graph.build_agent_graph(provider)
                config = {"configurable": {"thread_id": run_id}}
                started = time.perf_counter()
                state = graph.invoke(
                    agent_graph.initial_state(
                        case["message"], case["customer_id"], run_id, mode
                    ),
                    config=config,
                )
                latency_ms = (time.perf_counter() - started) * 1000
                tool_results = state.get("tool_results", [])
                refund_result = next(
                    (
                        item["result"]
                        for item in reversed(tool_results)
                        if item.get("name") == "get_refund_info"
                        and isinstance(item.get("result"), dict)
                        and item["result"].get("ok")
                    ),
                    None,
                )
                actual = {
                    "category": state.get("category"),
                    "tool_names": [item.get("name") for item in tool_results],
                    "draft_reply": state.get("draft_reply", ""),
                    "validation_flags": state.get("validation_flags", []),
                    "refund_eligible": (
                        refund_result.get("eligible") if refund_result else None
                    ),
                    "awaiting_approval": bool(state.get("__interrupt__"))
                    and state.get("approval_status") == "Pending",
                    "prompt_tokens": state.get("metrics", {}).get("prompt_tokens", 0),
                    "completion_tokens": state.get("metrics", {}).get(
                        "completion_tokens", 0
                    ),
                    "estimated_cost_usd": state.get("metrics", {}).get(
                        "estimated_cost_usd"
                    ),
                }
                reason_codes = evaluate_case(
                    case, actual, latency_ms, slow_threshold_ms
                )
                outputs.append(
                    {
                        "id": case["id"],
                        "expected": {
                            "category": case["expected_category"],
                            "tools": case["expected_tools"],
                        },
                        "actual": {
                            "category": actual["category"],
                            "tools": actual["tool_names"],
                            "awaiting_approval": actual["awaiting_approval"],
                            "validation_flags": actual["validation_flags"],
                            "refund_eligible": actual["refund_eligible"],
                        },
                        "reason_codes": reason_codes,
                        "latency_ms": round(latency_ms, 3),
                        "prompt_tokens": actual["prompt_tokens"],
                        "completion_tokens": actual["completion_tokens"],
                        "estimated_cost_usd": actual["estimated_cost_usd"],
                    }
                )
    finally:
        database.DATABASE_PATH = original_database_path
        if previous_as_of_date is None:
            os.environ.pop("REFUND_AS_OF_DATE", None)
        else:
            os.environ["REFUND_AS_OF_DATE"] = previous_as_of_date

    category_stats: dict[str, dict[str, int | float]] = {}
    for item in outputs:
        category = item["expected"]["category"]
        stats = category_stats.setdefault(category, {"count": 0, "correct": 0})
        stats["count"] += 1
        stats["correct"] += item["expected"]["category"] == item["actual"]["category"]
    for stats in category_stats.values():
        stats["accuracy"] = stats["correct"] / stats["count"]
    latencies = [float(item["latency_ms"]) for item in outputs]
    return {
        "case_count": len(outputs),
        "reference_date": reference_date,
        "accuracy": sum(
            item["expected"]["category"] == item["actual"]["category"]
            for item in outputs
        )
        / len(outputs),
        "accuracy_per_category": category_stats,
        "failures": [item for item in outputs if item["reason_codes"]],
        "latency_p50_ms": _percentile(latencies, 0.50),
        "latency_p95_ms": _percentile(latencies, 0.95),
        "prompt_tokens": sum(item["prompt_tokens"] for item in outputs),
        "completion_tokens": sum(item["completion_tokens"] for item in outputs),
        "total_tokens": sum(
            item["prompt_tokens"] + item["completion_tokens"] for item in outputs
        ),
        "total_cost_usd": (
            round(sum(
            float(item["estimated_cost_usd"])
            for item in outputs
            if item["estimated_cost_usd"] is not None
            ), 10)
            if any(item["estimated_cost_usd"] is not None for item in outputs)
            else None
        ),
        "cost_estimates_available": sum(
            item["estimated_cost_usd"] is not None for item in outputs
        ),
        "results": outputs,
    }


def _write_report(
    report: dict[str, Any], json_path: Path, markdown_path: Path
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    cost = report["total_cost_usd"]
    cost_display = "unavailable" if cost is None else f"${cost:.10f} USD"
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    lines = [
        "# Agent Evaluation",
        "",
        f"- Mode: `{report['mode']}`",
        f"- Cases: {report['case_count']}",
        f"- Evaluated (UTC): {report['timestamp_utc']}",
        f"- Refund-policy reference date: {report['reference_date']}",
        f"- Overall category accuracy: {report['accuracy']:.1%}",
        f"- Latency p50 / p95: {report['latency_p50_ms']:.3f} / "
        f"{report['latency_p95_ms']:.3f} ms",
        f"- Tokens (prompt / completion / total): {report['prompt_tokens']} / "
        f"{report['completion_tokens']} / {report['total_tokens']}",
        f"- Estimated cost: {cost_display} "
        f"({report['cost_estimates_available']} priced cases)",
        "",
        "## Accuracy per category",
        "",
        "| Category | Cases | Accuracy |",
        "|---|---:|---:|",
    ]
    for category, stats in sorted(report["accuracy_per_category"].items()):
        lines.append(
            f"| {category} | {stats['count']} | {stats['accuracy']:.1%} |"
        )
    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                "## Previous report comparison",
                "",
                f"- Previous accuracy: {comparison['previous_accuracy']:.1%}",
                f"- Accuracy change: {comparison['accuracy_delta']:+.1%}",
                f"- Regression: {'yes' if comparison['regression'] else 'no'}",
            ]
        )
    lines.extend(["", f"## Failures ({len(report['failures'])})", ""])
    if report["failures"]:
        lines.extend(
            [
                "| Case | Expected | Actual | Reason codes |",
                "|---|---|---|---|",
            ]
        )
        for failure in report["failures"]:
            lines.append(
                f"| {failure['id']} | `{json.dumps(failure['expected'], ensure_ascii=False)}` "
                f"| `{json.dumps(failure['actual'], ensure_ascii=False)}` "
                f"| {', '.join(failure['reason_codes'])} |"
            )
    else:
        lines.append("No cases failed the configured evaluator checks.")
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _threshold(value: str) -> float:
    try:
        threshold = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be between 0 and 1") from error
    if not 0 <= threshold <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return threshold


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("mock", "live"))
    parser.add_argument("--live", action="store_true", help="explicitly authorize live calls")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument(
        "--fail-under",
        type=_threshold,
        default=os.getenv("FLOWPILOT_EVAL_MIN_ACCURACY", "0.80"),
    )
    parser.add_argument(
        "--slow-threshold-ms",
        type=float,
        default=float(os.getenv("FLOWPILOT_EVAL_SLOW_THRESHOLD_MS", "5000")),
    )
    options = parser.parse_args(argv)
    mode = options.mode or os.getenv("LLM_MODE", "mock")
    if mode not in {"mock", "live"}:
        parser.error("LLM_MODE must be 'mock' or 'live'.")
    if mode == "live" and not (options.live or os.getenv("LLM_MODE") == "live"):
        parser.error("Live mode requires --live or LLM_MODE=live.")
    if mode == "live" and not os.getenv("OPENAI_API_KEY"):
        parser.error("Live mode requires OPENAI_API_KEY.")
    if options.limit is not None and options.limit < 1:
        parser.error("--limit must be at least 1.")
    if options.slow_threshold_ms <= 0:
        parser.error("--slow-threshold-ms must be positive.")
    cases = _read_dataset(DEFAULT_DATASET)
    limit = options.limit
    if mode == "live" and limit is None:
        limit = 5
    if limit is not None:
        cases = cases[:limit]
    evaluated = evaluate_cases(
        cases,
        mode,
        options.slow_threshold_ms,
        live_authorized=options.live,
    )
    output_dir = options.output if options.output.is_absolute() else ROOT / options.output
    report_path = output_dir / "eval_report.json"
    previous = None
    if report_path.exists():
        try:
            previous = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            parser.error(f"Previous report could not be read: {error}")
    report = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "mode": mode,
        "slow_threshold_ms": options.slow_threshold_ms,
        **{key: value for key, value in evaluated.items() if key != "results"},
    }
    if previous and isinstance(previous.get("accuracy"), (int, float)):
        delta = report["accuracy"] - float(previous["accuracy"])
        report["comparison"] = {
            "previous_accuracy": previous["accuracy"],
            "accuracy_delta": delta,
            "regression": delta < 0,
        }
    _write_report(report, report_path, output_dir / "eval_report.md")
    print(f"Mode: {mode}; cases: {report['case_count']}")
    print(f"Category accuracy: {report['accuracy']:.1%}")
    print(f"Reason-coded failures: {len(report['failures'])}")
    print(f"Reports: {report_path} and {output_dir / 'eval_report.md'}")
    return 0 if report["accuracy"] >= options.fail_under else 1


if __name__ == "__main__":
    raise SystemExit(main())
