"""Offline tests for LangGraph routing, validation, and human approval."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

import agent_graph
import database
from llm_response_generator import MockLLM


class AgentGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.original_path = database.DATABASE_PATH
        database.DATABASE_PATH = Path(self.directory.name) / "orders.db"
        self.addCleanup(self._restore_database)
        database.create_database()

    def _restore_database(self) -> None:
        database.DATABASE_PATH = self.original_path

    @staticmethod
    def _provider(script: list[dict] | None = None) -> MockLLM:
        response = "I can help with your request. A reviewer will check the details."
        return MockLLM({"Delivery Issue": response, "Refund Inquiry": response},
                       tool_call_script=script)

    def _start(
        self, message: str, customer_id: str = "CUST1001", provider: MockLLM | None = None
    ):
        run_id = "graph-test"
        graph = agent_graph.build_agent_graph(provider or self._provider(), MemorySaver())
        config = {"configurable": {"thread_id": run_id}}
        state = graph.invoke(
            agent_graph.initial_state(message, customer_id, run_id), config=config
        )
        return graph, config, state

    def test_refund_route_uses_owner_check_then_refund_tool_and_pauses(self) -> None:
        graph, config, state = self._start(
            "Please check refund ORD7391", customer_id="CUST1007"
        )
        self.assertEqual(state["category"], "Refund Inquiry")
        self.assertEqual(
            [item["name"] for item in state["tool_results"]],
            ["lookup_order", "get_refund_info"],
        )
        self.assertTrue(state["tool_results"][-1]["result"]["eligible"])
        self.assertTrue(state["__interrupt__"])
        self.assertEqual(state["approval_status"], "Pending")
        self.assertFalse(state["can_proceed"])

    def test_wrong_customer_does_not_get_refund_details(self) -> None:
        _graph, _config, state = self._start(
            "Please check refund ORD7391", customer_id="CUST1001"
        )
        self.assertEqual(state["tool_results"][0]["result"]["status"], "customer_mismatch")
        self.assertNotIn("get_refund_info", [item["name"] for item in state["tool_results"]])

    def test_hallucinated_order_fact_amount_and_promise_are_flagged(self) -> None:
        script = [
            {
                "tool_calls": [
                    {
                        "id": "lookup",
                        "function": {
                            "name": "lookup_order",
                            "arguments": '{"order_id":"ORD1042","customer_id":"CUST1001"}',
                        },
                    }
                ]
            },
            {"content": "ORD1042 was delivered today. We will refund $999.", "tool_calls": []},
        ]
        _graph, _config, state = self._start(
            "Where is my order ORD1042?", provider=self._provider(script)
        )
        self.assertIn("unsupported_fact", state["validation_flags"])
        self.assertIn("unsupported_amount", state["validation_flags"])
        self.assertIn("unsafe_promise", state["validation_flags"])
        self.assertEqual(state["draft_reply"], agent_graph.SAFE_FALLBACK)
        self.assertTrue(state["__interrupt__"])

    def test_edit_is_revalidated_then_can_be_approved(self) -> None:
        graph, config, state = self._start("Hello")
        run_id = state["run_id"]
        edited = graph.invoke(
            Command(resume={"decision": "edit", "draft_reply": "Thanks for contacting us."}),
            config=config,
        )
        self.assertTrue(edited["__interrupt__"])
        self.assertEqual(edited["draft_reply"], "Thanks for contacting us.")
        approved = graph.invoke(Command(resume={"decision": "approve"}), config=config)
        self.assertEqual(approved["approval_status"], "Approved")
        self.assertTrue(approved["can_proceed"])
        self.assertEqual(approved["run_id"], run_id)

    def test_rejection_never_allows_the_run_to_proceed(self) -> None:
        graph, config, _state = self._start("Hello")
        rejected = graph.invoke(Command(resume={"decision": "reject"}), config=config)
        self.assertEqual(rejected["approval_status"], "Rejected")
        self.assertFalse(rejected["can_proceed"])
        self.assertEqual(rejected["status"], "completed")


if __name__ == "__main__":
    unittest.main()
