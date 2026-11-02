"""Static contract tests for the credential-free n8n workflow export."""

import json
import unittest
from pathlib import Path


WORKFLOW_PATH = (
    Path(__file__).parent
    / "integrations"
    / "n8n"
    / "flowpilot-pending-response.json"
)


class TestN8NWorkflowExport(unittest.TestCase):
    def setUp(self):
        self.workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.nodes = {node["name"]: node for node in self.workflow["nodes"]}

    def test_workflow_is_valid_json_and_inactive(self):
        self.assertFalse(self.workflow["active"])
        self.assertEqual(len(self.nodes), len(self.workflow["nodes"]))

    def test_export_contains_no_credentials(self):
        def find_credential_fields(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key.lower() == "credentials":
                        return True
                    if find_credential_fields(child):
                        return True
            elif isinstance(value, list):
                return any(find_credential_fields(item) for item in value)
            return False

        self.assertFalse(find_credential_fields(self.workflow))

    def test_workflow_only_creates_a_template_pending_response(self):
        http_nodes = [
            node for node in self.workflow["nodes"]
            if node["type"] == "n8n-nodes-base.httpRequest"
        ]
        self.assertEqual(len(http_nodes), 1)
        parameters = http_nodes[0]["parameters"]
        self.assertEqual(parameters["method"], "POST")
        self.assertEqual(parameters["url"], "http://127.0.0.1:8000/workflow")
        self.assertIn("mode: 'template'", parameters["jsonBody"])
        self.assertEqual(
            self.nodes["Return pending result"]["type"],
            "n8n-nodes-base.respondToWebhook",
        )
        self.assertNotIn("/review", parameters["url"])
