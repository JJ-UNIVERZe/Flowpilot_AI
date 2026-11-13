"""Offline tests for strict order/refund tools and scripted tool calling."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

import database
import tools
from llm_response_generator import MockLLM, OpenAIProvider


class ToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.directory.name) / "orders.db"
        self.addCleanup(self._restore_database)
        self._date_patcher = mock.patch.dict(
            os.environ, {"REFUND_AS_OF_DATE": "2026-11-13"}
        )
        self._date_patcher.start()
        self.addCleanup(self._date_patcher.stop)
        database.create_database()

    def _restore_database(self) -> None:
        database.DATABASE_PATH = self.original_path

    def test_lookup_order_returns_details_for_owner_only(self) -> None:
        found = tools.lookup_order("ORD1042", "CUST1001")
        self.assertTrue(found["ok"])
        self.assertEqual(found["order"]["amount"], 129.99)
        self.assertEqual(tools.lookup_order("ORD1042", "CUST1002")["status"], "customer_mismatch")
        self.assertEqual(tools.lookup_order("ORD9999", "CUST1001")["status"], "not_found")

    def test_tool_argument_validation_is_strict(self) -> None:
        self.assertEqual(tools.lookup_order("ord1042", "CUST1001")["status"], "invalid_arguments")
        self.assertEqual(tools.get_refund_info("ORD10")["status"], "invalid_arguments")
        self.assertEqual(
            tools.execute_tool_call("lookup_order", '{"order_id":"ORD1042","customer_id":"CUST1001","extra":1}')["status"],
            "invalid_arguments",
        )
        self.assertEqual(tools.execute_tool_call("unknown", "{}")["status"], "unknown_tool")
        self.assertEqual(tools.execute_tool_call("get_refund_info", "{")["status"], "invalid_arguments")

    def test_refund_eligibility_and_existing_refund(self) -> None:
        eligible = tools.get_refund_info("ORD7391")
        self.assertTrue(eligible["eligible"])
        self.assertEqual(eligible["window_days"], 30)
        self.assertEqual(eligible["refundable_amount"], 75.0)
        self.assertEqual(tools.get_refund_info("ORD3187")["status"], "ineligible")
        self.assertIn("already exists", tools.get_refund_info("ORD3187")["reason"])

    def test_refund_policy_window_and_category_are_configurable(self) -> None:
        with mock.patch.dict(os.environ, {"REFUND_WINDOW_DAYS": "1"}):
            outside = tools.get_refund_info("ORD7391")
        self.assertFalse(outside["eligible"])
        self.assertIn("window has passed", outside["reason"])
        with mock.patch.dict(
            os.environ,
            {"NON_REFUNDABLE_CATEGORIES": "home", "REFUND_WINDOW_DAYS": "30"},
        ):
            excluded = tools.get_refund_info("ORD7391")
        self.assertFalse(excluded["eligible"])
        self.assertIn("non-refundable", excluded["reason"])

    def test_invalid_refund_configuration_is_reported(self) -> None:
        with mock.patch.dict(os.environ, {"REFUND_WINDOW_DAYS": "bad"}):
            result = tools.get_refund_info("ORD7391")
        self.assertEqual(result["status"], "policy_error")

    def test_refund_as_of_date_is_configurable_and_validated(self) -> None:
        with mock.patch.dict(os.environ, {"REFUND_AS_OF_DATE": "2026-12-15"}):
            expired = tools.get_refund_info("ORD7391")
        self.assertFalse(expired["eligible"])
        with mock.patch.dict(os.environ, {"REFUND_AS_OF_DATE": "not-a-date"}):
            invalid = tools.get_refund_info("ORD7391")
        self.assertEqual(invalid["status"], "policy_error")

    def test_every_tool_invocation_is_logged_with_arguments_and_status(self) -> None:
        tools.lookup_order("ORD1042", "CUST1001")
        tools.get_refund_info("ORD7391")
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection, connection:
            rows = connection.execute(
                "SELECT tool_name, arguments_json, result_status, duration_ms "
                "FROM tool_call_logs ORDER BY tool_call_id"
            ).fetchall()
        self.assertEqual([row[0] for row in rows], ["lookup_order", "get_refund_info"])
        self.assertEqual(json.loads(rows[0][1])["customer_id"], "CUST1001")
        self.assertEqual(rows[0][2], "found")
        self.assertTrue(all(row[3] >= 0 for row in rows))

    def test_mock_llm_runs_a_scripted_tool_call_sequence(self) -> None:
        mock_llm = MockLLM(
            {"General Inquiry": "Done."},
            tool_call_script=[
                {
                    "tool_calls": [
                        {
                            "id": "step-1",
                            "function": {
                                "name": "lookup_order",
                                "arguments": '{"order_id":"ORD1042","customer_id":"CUST1001"}',
                            },
                        }
                    ]
                }
            ],
        )
        response = mock_llm.generate_with_tools(
            [{"role": "user", "content": "Help"}], tools.TOOL_SCHEMAS
        )
        self.assertEqual(response["tool_calls"][0]["id"], "step-1")
        final = mock_llm.generate_with_tools(
            [{"role": "user", "content": "- Request category: General Inquiry"}], []
        )
        self.assertEqual(final["content"], "Done.")

    def test_openai_provider_normalizes_tool_calls(self) -> None:
        provider = OpenAIProvider()
        function_call = SimpleNamespace(
            name="lookup_order", arguments='{"order_id":"ORD1042"}'
        )
        call = SimpleNamespace(id="call-1", function=function_call)
        message = SimpleNamespace(content=None, tool_calls=[call])
        response = SimpleNamespace(choices=[SimpleNamespace(message=message)])
        provider._client = mock.Mock()
        provider._client.chat.completions.create.return_value = response
        actual = provider.generate_with_tools([{"role": "user", "content": "help"}], tools.TOOL_SCHEMAS)
        self.assertEqual(actual["tool_calls"][0]["function"]["name"], "lookup_order")
        self.assertEqual(actual["tool_calls"][0]["function"]["arguments"], '{"order_id":"ORD1042"}')
        provider._client.chat.completions.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
