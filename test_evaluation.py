"""Regression tests for the offline labelled evaluation harness."""

from __future__ import annotations

import json
import os
import socket
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import flowpilot
from llm_response_generator import MockLLM
from scripts import evaluate

ROOT = Path(__file__).resolve().parent
CASES_PATH = ROOT / "evals" / "cases.json"
BASELINE_PATH = ROOT / "evals" / "baseline.json"
VALID_OUTCOMES = {
    "order_found",
    "order_not_found",
    "malformed_order_id",
    "missing_order_id",
    "no_order_needed",
}
VALID_TAGS = {
    "clear",
    "ambiguous",
    "typo",
    "missing_id",
    "malformed_id",
    "nonexistent_order",
    "injection",
    "edge",
    "known_limitation",
    "policy",
}
EXPECTED_METRICS = {
    "classification_accuracy",
    "order_id_extraction_accuracy",
    "outcome_accuracy",
    "safety_pass_rate",
    "policy_retrieval_accuracy",
    "policy_retrieval_cases",
    "mean_processing_time_ms",
    "p95_processing_time_ms",
    "accuracy_per_tag",
    "confusion_matrix",
}


def _load_cases() -> list[dict]:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


class EvaluationHarnessTests(unittest.TestCase):
    def test_cases_are_valid_unique_and_cover_all_categories(self) -> None:
        cases = _load_cases()
        category_set = {category for _, category, _ in flowpilot.RULES}
        category_set.add(flowpilot.FALLBACK_CATEGORY)
        ids = [case.get("id") for case in cases]
        required_fields = {
            "id",
            "message",
            "expected_category",
            "expected_order_id",
            "expected_outcome",
            "forbidden_phrases",
            "tags",
        }

        self.assertGreaterEqual(len(cases), 60)
        policy_cases = [case for case in cases if "policy" in case["tags"]]
        self.assertEqual(len(policy_cases), 15)
        self.assertTrue(all(case.get("expected_policy_doc") for case in policy_cases))
        self.assertGreaterEqual(max(len(case["message"]) for case in cases), 5000)
        self.assertEqual(len(ids), len(set(ids)))
        for case in cases:
            with self.subTest(case=case.get("id")):
                self.assertTrue(required_fields <= case.keys())
                self.assertIsInstance(case["message"], str)
                self.assertIn(case["expected_category"], category_set)
                self.assertIn(case["expected_outcome"], VALID_OUTCOMES)
                self.assertTrue(
                    case["expected_order_id"] is None
                    or isinstance(case["expected_order_id"], str)
                )
                self.assertIsInstance(case["forbidden_phrases"], list)
                self.assertTrue(all(isinstance(value, str) for value in case["forbidden_phrases"]))
                self.assertIsInstance(case["tags"], list)
                self.assertTrue(set(case["tags"]) <= VALID_TAGS)

        self.assertEqual(category_set, set(evaluate.REAL_CATEGORIES))

    def test_evaluator_returns_all_metrics_and_restores_database_path(self) -> None:
        cases = _load_cases()
        subset = [cases[0], cases[4], cases[7], cases[59]]
        original_database_path = database.DATABASE_PATH

        result = evaluate.evaluate_cases(subset, mode="template")

        self.assertEqual(database.DATABASE_PATH, original_database_path)
        self.assertEqual(set(result["metrics"]), EXPECTED_METRICS)
        self.assertEqual(result["metrics"]["classification_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["order_id_extraction_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["outcome_accuracy"], 1.0)
        self.assertEqual(result["metrics"]["safety_pass_rate"], 1.0)
        self.assertEqual(result["metrics"]["policy_retrieval_cases"], 0)
        self.assertEqual(result["metrics"]["policy_retrieval_accuracy"], 0.0)
        self.assertIsInstance(result["metrics"]["mean_processing_time_ms"], float)
        self.assertIsInstance(result["metrics"]["p95_processing_time_ms"], float)
        self.assertIn("clear", result["metrics"]["accuracy_per_tag"])
        self.assertIn("Delivery Issue", result["metrics"]["confusion_matrix"])
        self.assertEqual(result["failures"], [])

    def test_classification_accuracy_meets_first_measurement_baseline(self) -> None:
        baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
        result = evaluate.evaluate_cases(_load_cases(), mode="template")

        self.assertGreaterEqual(
            result["metrics"]["classification_accuracy"],
            baseline["classification_accuracy"],
        )
        self.assertEqual(result["metrics"]["policy_retrieval_cases"], 15)
        self.assertEqual(result["metrics"]["policy_retrieval_accuracy"], 1.0)

    def test_mock_mode_uses_no_network_or_openai_provider(self) -> None:
        cases = _load_cases()[:3]
        provider = evaluate._provider_for_mode("mock")
        self.assertIsInstance(provider, MockLLM)
        with (
            patch.object(socket.socket, "connect") as network_connect,
            patch.object(flowpilot, "get_openai_provider") as openai_provider,
        ):
            result = evaluate.evaluate_cases(cases, mode="mock")

        network_connect.assert_not_called()
        openai_provider.assert_not_called()
        self.assertEqual(os.environ.get("OPENAI_API_KEY"), None)
        self.assertEqual(result["metrics"]["safety_pass_rate"], 1.0)


if __name__ == "__main__":
    unittest.main()
