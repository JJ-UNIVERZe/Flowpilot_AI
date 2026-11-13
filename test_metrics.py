"""Persistence, aggregation, and pricing tests for agent-run metrics."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import database
import metrics


class MetricsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.directory.name) / "orders.db"
        self.addCleanup(self._restore_database)
        database.create_database()

    def _restore_database(self) -> None:
        database.DATABASE_PATH = self.original_path

    def test_mock_cost_is_zero_and_live_cost_uses_configured_prices(self) -> None:
        self.assertEqual(metrics.estimate_cost_usd(300, 100, "mock", "mock"), 0.0)
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_PRICE_INPUT_PER_1M": "2",
                "OPENAI_PRICE_OUTPUT_PER_1M": "4",
            },
        ):
            actual = metrics.estimate_cost_usd(500, 250, "fictional-model", "live")
        self.assertEqual(actual, 0.002)
        with mock.patch.dict(os.environ, {"OPENAI_PRICE_INPUT_PER_1M": ""}):
            self.assertIsNone(
                metrics.estimate_cost_usd(500, 250, "fictional-model", "live")
            )

    def test_record_upserts_and_summary_computes_latency_and_token_totals(self) -> None:
        base = {
            "node_latency_ms": {"classify": 1.0, "agent": 2.0},
            "llm_latency_ms": 3.0,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "tool_call_count": 1,
        }
        for index, (latency, approval_wait) in enumerate(
            zip((10.0, 20.0, 30.0), (100.0, 200.0, 300.0))
        ):
            metrics.record_run_metrics(
                f"run-{index}", base, "mock", "mock", latency, approval_wait
            )
        metrics.record_run_metrics("run-0", base, "mock", "mock", 10.0, 100.0)
        summary = metrics.get_metrics_summary()
        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["avg_latency_ms"], 20.0)
        self.assertEqual(summary["p50_latency_ms"], 20.0)
        self.assertEqual(summary["p95_latency_ms"], 30.0)
        self.assertEqual(summary["avg_approval_wait_ms"], 200.0)
        self.assertEqual(summary["total_tokens"], 45)
        self.assertEqual(summary["total_cost_usd"], 0.0)
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            row = connection.execute(
                "SELECT node_latencies_json FROM run_metrics WHERE run_id = 'run-0'"
            ).fetchone()
        self.assertIn('"agent": 2.0', row[0])

    def test_existing_metrics_database_migrates_approval_wait_column_once(self) -> None:
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection, connection:
            connection.execute("ALTER TABLE run_metrics DROP COLUMN approval_wait_ms")
            connection.execute(
                """
                INSERT INTO run_metrics (
                    run_id, mode, model_name, total_latency_ms,
                    node_latencies_json, llm_latency_ms, prompt_tokens,
                    completion_tokens, tool_call_count, estimated_cost_usd
                ) VALUES ('legacy-run', 'mock', 'mock', 12, '{}', 0, 0, 0, 0, 0)
                """
            )

        database.create_database()
        database.create_database()
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            row = connection.execute(
                "SELECT total_latency_ms, approval_wait_ms FROM run_metrics "
                "WHERE run_id = 'legacy-run'"
            ).fetchone()
        self.assertEqual(row, (12.0, 0.0))

    def test_invalid_prices_and_negative_metrics_are_rejected(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "OPENAI_PRICE_INPUT_PER_1M": "-1",
                "OPENAI_PRICE_OUTPUT_PER_1M": "1",
            },
        ):
            with self.assertRaises(ValueError):
                metrics.estimate_cost_usd(1, 1, "model", "live")
        with self.assertRaises(ValueError):
            metrics.record_run_metrics(
                "bad-run",
                {
                    "node_latency_ms": {},
                    "llm_latency_ms": 0.0,
                    "prompt_tokens": -1,
                    "completion_tokens": 0,
                    "tool_call_count": 0,
                },
                "mock",
                "mock",
                1.0,
            )


if __name__ == "__main__":
    unittest.main()
