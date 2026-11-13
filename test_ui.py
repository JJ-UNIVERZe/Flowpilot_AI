"""Streamlit API-client tests; all HTTP requests are mocked."""

import unittest
from pathlib import Path
from unittest import mock

import httpx
from streamlit.proto import TextInput_pb2
from streamlit.testing.v1 import AppTest

import ui


class TestStreamlitAPIClient(unittest.TestCase):
    def _dashboard_response(self, url, evaluation_status=200, stats_status=200):
        if url.endswith("/evaluation"):
            status_code = evaluation_status
            payload = self._evaluation_payload()
        elif url.endswith("/metrics/summary"):
            status_code = 200
            payload = {
                "count": 1,
                "avg_latency_ms": 10.0,
                "p50_latency_ms": 10.0,
                "p95_latency_ms": 10.0,
                "total_prompt_tokens": 0,
                "total_completion_tokens": 0,
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "cost_estimates_available": 1,
            }
        else:
            status_code = stats_status
            payload = {
                "total_decisions": 3,
                "by_decision": {"Approved": 2, "Rejected": 1},
                "by_category": {"Delivery Issue": 1, "Refund Inquiry": 2},
            }
        response = mock.Mock()
        response.status_code = status_code
        response.is_success = 200 <= status_code < 300
        response.json.return_value = payload
        return response

    @staticmethod
    def _evaluation_payload():
        return {
            "timestamp_utc": "2026-11-04T00:00:00Z",
            "mode": "template",
            "case_count": 2,
            "metrics": {
                "classification_accuracy": 1.0,
                "order_id_extraction_accuracy": 1.0,
                "outcome_accuracy": 0.5,
                "safety_pass_rate": 1.0,
                "mean_processing_time_ms": 12.5,
                "p95_processing_time_ms": 20.0,
                "accuracy_per_tag": {
                    "clear": {"count": 1, "classification_accuracy": 1.0},
                    "typo": {"count": 1, "classification_accuracy": 0.0},
                },
                "confusion_matrix": {
                    "Delivery Issue": {"Delivery Issue": 1, "Refund Inquiry": 0},
                    "Refund Inquiry": {"Delivery Issue": 1, "Refund Inquiry": 0},
                },
            },
            "failures": [
                {
                    "id": "case-ui",
                    "expected": {
                        "category": "Refund Inquiry",
                        "order_id": "ORD1042",
                        "outcome": "order_found",
                    },
                    "actual": {
                        "category": "Delivery Issue",
                        "order_id": "ORD1042",
                        "outcome": "order_found",
                    },
                    "safety_pass": True,
                }
            ],
        }

    def _mock_dashboard_requests(
        self, evaluation_status=200, stats_status=200, unreachable=False
    ):
        def request(method, url, **kwargs):
            if unreachable and url.endswith("/evaluation"):
                raise httpx.ConnectError("local API unavailable")
            return self._dashboard_response(url, evaluation_status, stats_status)

        return mock.patch.object(
            ui.httpx,
            "request",
            side_effect=request,
        )

    def test_ui_renders_and_openai_requires_explicit_confirmation(self):
        with self._mock_dashboard_requests():
            app = AppTest.from_file(str(Path(ui.__file__))).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.selectbox), 3)

        with self._mock_dashboard_requests():
            app.selectbox[0].select("openai").run()
            self.assertTrue(app.button[0].disabled)
            app.checkbox[0].check().run()
            self.assertFalse(app.button[0].disabled)
            self.assertFalse(app.exception)

    def test_dashboard_tabs_render_sample_evaluation_and_review_stats(self):
        with self._mock_dashboard_requests() as request:
            app = AppTest.from_file(str(Path(ui.__file__))).run()

        self.assertFalse(app.exception)
        self.assertGreaterEqual(len(app.metric), 7)
        self.assertEqual(len(app.get("vega_lite_chart")), 3)
        self.assertGreaterEqual(len(app.dataframe), 2)
        reviewer_input = next(
            element for element in app.text_input if element.label == "Reviewer key"
        )
        input_type = TextInput_pb2.TextInput.DESCRIPTOR.fields_by_name[
            "type"
        ].enum_type.values_by_number[reviewer_input.proto.type]
        self.assertEqual(input_type.name, "PASSWORD")
        self.assertTrue(
            any(
                "Measured on 2 synthetic cases in template/mock mode. Not live OpenAI."
                in str(element.value)
                for element in app.info
            )
        )
        self.assertEqual(
            request.call_args_list[0].kwargs["headers"],
            {"X-Review-API-Key": ""},
        )

    def test_evaluation_tab_shows_friendly_missing_file_message(self):
        with self._mock_dashboard_requests(evaluation_status=404):
            app = AppTest.from_file(str(Path(ui.__file__))).run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(
                "Evaluation results are not available yet. Run scripts/evaluate.py first."
                in str(element.value)
                for element in app.error
            )
        )

    def test_dashboard_shows_friendly_authentication_message(self):
        with self._mock_dashboard_requests(evaluation_status=401, stats_status=403):
            app = AppTest.from_file(str(Path(ui.__file__))).run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(
                "Enter a valid reviewer key in the sidebar"
                in str(element.value)
                for element in app.error
            )
        )

    def test_dashboard_shows_friendly_rate_limit_and_service_messages(self):
        examples = (
            (429, 200, "too many requests"),
            (200, 503, "temporarily unavailable"),
        )
        for evaluation_status, stats_status, message in examples:
            with self.subTest(status=message):
                with self._mock_dashboard_requests(
                    evaluation_status, stats_status
                ):
                    app = AppTest.from_file(str(Path(ui.__file__))).run()
                self.assertFalse(app.exception)
                self.assertTrue(
                    any(
                        message in str(element.value).lower()
                        for element in app.error
                    )
                )

    def test_dashboard_shows_friendly_unreachable_message(self):
        with self._mock_dashboard_requests(unreachable=True):
            app = AppTest.from_file(str(Path(ui.__file__))).run()

        self.assertFalse(app.exception)
        self.assertTrue(
            any(
                "FlowPilot API is unreachable"
                in str(element.value)
                for element in app.error
            )
        )

    def test_request_api_sends_payload_and_reviewer_header(self):
        response = mock.Mock()
        response.is_success = True
        response.json.return_value = {"response_id": "test-id"}
        with mock.patch.object(ui.httpx, "request", return_value=response) as request:
            result = ui._request_api(
                "POST",
                "/responses/test-id/review",
                {"decision": "Approved"},
                {"X-Review-API-Key": "never-display-this"},
            )

        self.assertEqual(result, {"response_id": "test-id"})
        self.assertEqual(request.call_args.args[:2], (
            "POST", f"{ui.API_BASE_URL}/responses/test-id/review"
        ))
        self.assertEqual(
            request.call_args.kwargs["headers"],
            {"X-Review-API-Key": "never-display-this"},
        )

    def test_http_failure_does_not_expose_request_or_key(self):
        response = mock.Mock()
        response.is_success = False
        response.status_code = 401
        with mock.patch.object(ui.httpx, "request", return_value=response):
            with self.assertRaises(ui.APIRequestError) as caught:
                ui._request_api(
                    "POST",
                    "/responses/id/review",
                    {"decision": "Approved"},
                    {"X-Review-API-Key": "secret-review-key"},
                )
        self.assertEqual(
            str(caught.exception),
            "FlowPilot API rejected the request (HTTP 401).",
        )
        self.assertNotIn("secret-review-key", str(caught.exception))

    def test_network_failure_is_reported_without_request_details(self):
        request = httpx.ConnectError("sensitive connection details")
        with mock.patch.object(ui.httpx, "request", side_effect=request):
            with self.assertRaises(ui.APIRequestError) as caught:
                ui._request_api("POST", "/workflow", {"message": "test"})
        self.assertEqual(str(caught.exception), "FlowPilot API is unavailable.")
        self.assertNotIn("sensitive connection details", str(caught.exception))

    def test_invalid_json_response_is_reported(self):
        response = mock.Mock()
        response.is_success = True
        response.json.side_effect = ValueError("invalid json")
        with mock.patch.object(ui.httpx, "request", return_value=response):
            with self.assertRaisesRegex(
                ui.APIRequestError, "returned an invalid response"
            ):
                ui._request_api("GET", "/health")


if __name__ == "__main__":
    unittest.main()
