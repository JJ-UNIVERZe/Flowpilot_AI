"""HTTP API tests using FastAPI's in-process client and temporary SQLite."""

import os
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import api
import database


class TestHTTPAPI(unittest.TestCase):
    def setUp(self):
        self._temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp_dir.cleanup)
        self._original_database_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self._temp_dir.name) / "orders.db"
        self.addCleanup(self._restore_database_path)
        database.create_database()
        self._review_key_patcher = mock.patch.dict(
            os.environ, {"REVIEW_API_KEY": "test-review-key"}
        )
        self._review_key_patcher.start()
        self.addCleanup(self._review_key_patcher.stop)
        with api._pending_lock:
            api.pending_responses.clear()
        with api._agent_runs_lock:
            api.agent_runs.clear()
        self.client = TestClient(api.app)

    def _restore_database_path(self):
        database.DATABASE_PATH = self._original_database_path

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_evaluation_endpoint_requires_reviewer_key(self):
        response = self.client.get("/evaluation")
        self.assertEqual(response.status_code, 401)

    def test_evaluation_endpoint_returns_configured_json_file(self):
        results_path = Path(self._temp_dir.name) / "evaluation.json"
        expected = {"mode": "template", "case_count": 1}
        results_path.write_text(json.dumps(expected), encoding="utf-8")
        with mock.patch.dict(
            os.environ, {"EVALUATION_RESULTS_PATH": str(results_path)}
        ):
            response = self.client.get(
                "/evaluation", headers={"X-Review-API-Key": "test-review-key"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)

    def test_evaluation_endpoint_returns_clear_404_when_file_is_missing(self):
        missing_path = Path(self._temp_dir.name) / "missing-evaluation.json"
        with mock.patch.dict(
            os.environ, {"EVALUATION_RESULTS_PATH": str(missing_path)}
        ):
            response = self.client.get(
                "/evaluation", headers={"X-Review-API-Key": "test-review-key"}
            )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Run scripts/evaluate.py first", response.json()["detail"])

    def test_evaluation_endpoint_returns_safe_500_for_invalid_json(self):
        results_path = Path(self._temp_dir.name) / "invalid-evaluation.json"
        results_path.write_text("{invalid", encoding="utf-8")
        with mock.patch.dict(
            os.environ, {"EVALUATION_RESULTS_PATH": str(results_path)}
        ):
            response = self.client.get(
                "/evaluation", headers={"X-Review-API-Key": "test-review-key"}
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"], "Evaluation results could not be read."
        )
        self.assertNotIn(str(results_path), response.text)

    def test_evaluation_endpoint_returns_safe_500_for_invalid_encoding(self):
        results_path = Path(self._temp_dir.name) / "invalid-encoding.json"
        results_path.write_bytes(b"\xff")
        with mock.patch.dict(
            os.environ, {"EVALUATION_RESULTS_PATH": str(results_path)}
        ):
            response = self.client.get(
                "/evaluation", headers={"X-Review-API-Key": "test-review-key"}
            )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.json()["detail"], "Evaluation results could not be read."
        )
        self.assertNotIn(str(results_path), response.text)

    def test_stats_endpoint_requires_reviewer_key(self):
        response = self.client.get("/stats")
        self.assertEqual(response.status_code, 401)

    def test_stats_endpoint_counts_decisions_and_categories_without_writes(self):
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            connection.execute(
                "ALTER TABLE approval_logs ADD COLUMN category TEXT"
            )
            connection.executemany(
                """
                INSERT INTO approval_logs
                    (customer_message, generated_response, approval_status,
                     reviewed_at, category)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    ("message 1", "response 1", "Approved", "now", "Refund Inquiry"),
                    ("message 2", "response 2", "Approved", "now", "Delivery Issue"),
                    ("message 3", "response 3", "Rejected", "now", "Refund Inquiry"),
                ],
            )
            connection.commit()
        before = hashlib.sha256(database.DATABASE_PATH.read_bytes()).digest()

        response = self.client.get(
            "/stats", headers={"X-Review-API-Key": "test-review-key"}
        )

        after = hashlib.sha256(database.DATABASE_PATH.read_bytes()).digest()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "total_decisions": 3,
                "by_decision": {"Approved": 2, "Rejected": 1},
                "by_category": {"Delivery Issue": 1, "Refund Inquiry": 2},
            },
        )
        self.assertEqual(after, before)

    def test_stats_endpoint_omits_categories_when_not_recorded(self):
        response = self.client.get(
            "/stats", headers={"X-Review-API-Key": "test-review-key"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "total_decisions": 0,
                "by_decision": {"Approved": 0, "Rejected": 0},
                "by_category": {},
            },
        )

    def test_workflow_response_stays_pending_until_human_review(self):
        response = self.client.post(
            "/workflow",
            json={
                "message": "My order ORD1042 has not arrived.",
                "mode": "template",
            },
        )
        self.assertEqual(response.status_code, 201)
        result = response.json()["result"]
        self.assertEqual(result["approval_status"], "Pending")
        self.assertFalse(result["can_proceed"])
        self.assertIsNone(result["approval"])
        self.assertIn(response.json()["response_id"], api.pending_responses)

    def test_workflow_rejects_empty_and_overlong_messages(self):
        for message in ("", "x" * 4001):
            with self.subTest(message_length=len(message)):
                response = self.client.post(
                    "/workflow", json={"message": message}
                )
                self.assertEqual(response.status_code, 422)
        self.assertFalse(api.pending_responses)

    def test_review_endpoint_requires_configured_key(self):
        created = self.client.post(
            "/workflow", json={"message": "Where is order ORD1042?"}
        ).json()
        with mock.patch.dict(os.environ, {"REVIEW_API_KEY": ""}):
            response = self.client.post(
                f"/responses/{created['response_id']}/review",
                json={"decision": "Approved"},
                headers={"X-Review-API-Key": "test-review-key"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            api.pending_responses[created["response_id"]]["approval_status"],
            "Pending",
        )

    def test_review_endpoint_rejects_invalid_key(self):
        created = self.client.post(
            "/workflow", json={"message": "Where is order ORD1042?"}
        ).json()
        response = self.client.post(
            f"/responses/{created['response_id']}/review",
            json={"decision": "Approved"},
            headers={"X-Review-API-Key": "incorrect"},
        )
        self.assertEqual(response.status_code, 401)
        self.assertNotIn("incorrect", response.json()["detail"])

    def test_authenticated_human_approval_is_audited(self):
        created = self.client.post(
            "/workflow", json={"message": "Where is order ORD1042?"}
        ).json()
        response = self.client.post(
            f"/responses/{created['response_id']}/review",
            json={"decision": "Approved"},
            headers={"X-Review-API-Key": "test-review-key"},
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()["result"]
        self.assertEqual(result["approval_status"], "Approved")
        self.assertTrue(result["can_proceed"])
        self.assertIsNotNone(result["approval"])
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            row = connection.execute(
                "SELECT approval_status FROM approval_logs"
            ).fetchone()
        self.assertEqual(row, ("Approved",))
        self.assertNotIn(created["response_id"], api.pending_responses)

    def test_authenticated_rejection_is_audited_and_cannot_proceed(self):
        created = self.client.post(
            "/workflow", json={"message": "Where is order ORD1042?"}
        ).json()
        response = self.client.post(
            f"/responses/{created['response_id']}/review",
            json={"decision": "Rejected"},
            headers={"X-Review-API-Key": "test-review-key"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"]["approval_status"], "Rejected")
        self.assertFalse(response.json()["result"]["can_proceed"])

    def test_approval_does_not_change_order_refund_or_payment_data(self):
        before = (
            database.get_order("ORD2050"),
            database.get_refund_status("ORD2050"),
            database.get_payment_status("ORD2050"),
        )
        created = self.client.post(
            "/workflow", json={"message": "I want a refund for ORD2050."}
        ).json()
        response = self.client.post(
            f"/responses/{created['response_id']}/review",
            json={"decision": "Approved"},
            headers={"X-Review-API-Key": "test-review-key"},
        )
        self.assertEqual(response.status_code, 200)
        after = (
            database.get_order("ORD2050"),
            database.get_refund_status("ORD2050"),
            database.get_payment_status("ORD2050"),
        )
        self.assertEqual(after, before)

    def test_invalid_decision_does_not_consume_pending_response(self):
        created = self.client.post(
            "/workflow", json={"message": "Where is order ORD1042?"}
        ).json()
        response = self.client.post(
            f"/responses/{created['response_id']}/review",
            json={"decision": "Pending"},
            headers={"X-Review-API-Key": "test-review-key"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn(created["response_id"], api.pending_responses)

    def test_openai_mode_without_key_falls_back_to_template(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            response = self.client.post(
                "/workflow",
                json={
                    "message": "My order ORD1042 has not arrived.",
                    "mode": "openai",
                },
            )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["result"]["customer_response"])
        self.assertFalse(response.json()["result"]["can_proceed"])

    def test_agent_run_uses_mock_tool_call_and_stops_at_approval(self):
        response = self.client.post(
            "/agent/run",
            json={
                "message": "Where is my order ORD1042?",
                "customer_id": "CUST1001",
            },
        )
        self.assertEqual(response.status_code, 201)
        run = response.json()
        self.assertEqual(run["status"], "awaiting_approval")
        self.assertEqual(run["approval_status"], "Pending")
        self.assertFalse(run["can_proceed"])
        self.assertEqual(run["tool_results"][0]["result"]["status"], "found")

    def test_agent_run_requires_customer_id_and_live_key(self):
        missing_customer = self.client.post(
            "/agent/run", json={"message": "Where is order ORD1042?"}
        )
        self.assertEqual(missing_customer.status_code, 422)
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            live = self.client.post(
                "/agent/run",
                json={
                    "message": "Where is order ORD1042?",
                    "customer_id": "CUST1001",
                    "mode": "live",
                },
            )
        self.assertEqual(live.status_code, 503)

    def test_agent_run_status_requires_reviewer_authentication(self):
        created = self.client.post(
            "/agent/run",
            json={"message": "Hello", "customer_id": "CUST1001"},
        ).json()
        response = self.client.get(f"/agent/runs/{created['run_id']}")
        self.assertEqual(response.status_code, 401)

    def test_agent_edit_revalidates_then_reject_is_audited(self):
        created = self.client.post(
            "/agent/run",
            json={"message": "Hello", "customer_id": "CUST1001"},
        ).json()
        run_id = created["run_id"]
        headers = {"X-Review-API-Key": "test-review-key"}
        invalid_edit = self.client.post(
            f"/agent/runs/{run_id}/approve",
            json={"action": "edit"},
            headers=headers,
        )
        self.assertEqual(invalid_edit.status_code, 422)
        edit = self.client.post(
            f"/agent/runs/{run_id}/approve",
            json={"action": "edit", "draft_reply": "Thanks for reaching out."},
            headers=headers,
        )
        self.assertEqual(edit.status_code, 200)
        self.assertEqual(edit.json()["status"], "awaiting_approval")
        rejected = self.client.post(
            f"/agent/runs/{run_id}/reject", headers=headers
        )
        self.assertEqual(rejected.status_code, 200)
        self.assertEqual(rejected.json()["approval_status"], "Rejected")
        self.assertFalse(rejected.json()["can_proceed"])
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            decisions = connection.execute(
                "SELECT approval_status FROM approval_logs"
            ).fetchall()
        self.assertEqual(decisions, [("Rejected",)])

    def test_agent_approval_requires_reviewer_key_and_is_audited(self):
        created = self.client.post(
            "/agent/run",
            json={"message": "Hello", "customer_id": "CUST1001"},
        ).json()
        run_id = created["run_id"]
        denied = self.client.post(
            f"/agent/runs/{run_id}/approve",
            json={"action": "approve"},
        )
        self.assertEqual(denied.status_code, 401)
        approved = self.client.post(
            f"/agent/runs/{run_id}/approve",
            json={"action": "approve"},
            headers={"X-Review-API-Key": "test-review-key"},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.json()["approval_status"], "Approved")
        self.assertTrue(approved.json()["can_proceed"])
