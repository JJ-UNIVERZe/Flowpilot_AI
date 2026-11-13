"""Offline tests for the agent evaluation dataset, evaluators, and reports."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_eval
from evals.evaluators import REASON_CODES, evaluate_case


class AgentEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cases = run_eval._read_dataset(run_eval.DEFAULT_DATASET)

    def test_dataset_has_at_least_sixty_unique_cases_and_required_edges(self) -> None:
        self.assertGreaterEqual(len(self.cases), 60)
        ids = {case["id"] for case in self.cases}
        self.assertEqual(len(ids), len(self.cases))
        self.assertTrue(
            {
                "agent-nonexistent-order",
                "agent-other-customer-status",
                "agent-refund-outside-window",
                "agent-already-refunded",
                "agent-refund-missing-id",
                "agent-vague-request",
                "agent-mixed-malayalam-style",
                "agent-prompt-injection",
                "agent-private-customer-details",
                "agent-unauthorized-discount",
                "agent-very-long-input",
            }
            <= ids
        )
        self.assertGreaterEqual(
            max(len(case["message"]) for case in self.cases), 5000
        )

    def test_each_evaluator_reason_code_is_reported(self) -> None:
        case = {
            "expected_category": "Refund Inquiry",
            "expected_tools": ["lookup_order", "get_refund_info"],
            "must_escalate": True,
            "expected_refund_eligible": False,
            "policy_blocked_terms": ["discount"],
            "tags": ["injection"],
        }
        actual = {
            "category": "Delivery Issue",
            "tool_names": ["other_tool"],
            "validation_flags": ["unsupported_amount", "unsafe_promise"],
            "draft_reply": "We will refund everything and give you a discount.",
            "refund_eligible": True,
            "awaiting_approval": False,
        }
        self.assertEqual(
            set(evaluate_case(case, actual, 200, 100)),
            REASON_CODES,
        )

    def test_clean_case_has_no_failure_reason(self) -> None:
        case = {
            "expected_category": "Delivery Issue",
            "expected_tools": ["lookup_order"],
            "must_escalate": True,
            "tags": [],
        }
        actual = {
            "category": "Delivery Issue",
            "tool_names": ["lookup_order"],
            "validation_flags": [],
            "draft_reply": "A reviewer will check the details.",
            "awaiting_approval": True,
        }
        self.assertEqual(evaluate_case(case, actual, 20, 100), [])

    def test_mock_evaluation_is_offline_and_reports_category_latency_and_tokens(self) -> None:
        selected = [
            next(case for case in self.cases if case["id"] == case_id)
            for case_id in (
                "agent-other-customer-status",
                "agent-refund-outside-window",
                "agent-mixed-malayalam-style",
                "agent-prompt-injection",
                "agent-vague-request",
            )
        ]
        result = run_eval.evaluate_cases(selected, "mock", slow_threshold_ms=60000)
        self.assertEqual(result["case_count"], 5)
        self.assertGreaterEqual(result["accuracy"], 0.8)
        self.assertGreater(result["latency_p95_ms"], 0)
        self.assertEqual(result["total_tokens"], 0)
        self.assertEqual(result["total_cost_usd"], 0.0)
        self.assertIn("Refund Inquiry", result["accuracy_per_category"])

    def test_live_provider_requires_explicit_live_authorization(self) -> None:
        with mock.patch.dict(
            os.environ, {"LLM_MODE": "mock", "OPENAI_API_KEY": ""}
        ):
            with self.assertRaisesRegex(ValueError, "--live or LLM_MODE=live"):
                run_eval._provider("live")

    def test_cli_writes_both_reports_and_compares_previous_accuracy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "eval_report.json").write_text(
                json.dumps({"accuracy": 1.0}), encoding="utf-8"
            )
            result = run_eval.main(
                [
                    "--mode",
                    "mock",
                    "--limit",
                    "1",
                    "--output",
                    str(output),
                    "--fail-under",
                    "0",
                ]
            )
            self.assertEqual(result, 0)
            report = json.loads((output / "eval_report.json").read_text(encoding="utf-8"))
            self.assertIn("comparison", report)
            self.assertTrue((output / "eval_report.md").is_file())
            self.assertIn("Previous report comparison", (output / "eval_report.md").read_text())


if __name__ == "__main__":
    unittest.main()
