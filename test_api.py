"""HTTP API tests using FastAPI's in-process client and temporary SQLite."""

import os
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
        self.client = TestClient(api.app)

    def _restore_database_path(self):
        database.DATABASE_PATH = self._original_database_path

    def test_health_endpoint(self):
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

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
