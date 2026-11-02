"""Streamlit API-client tests; all HTTP requests are mocked."""

import unittest
from pathlib import Path
from unittest import mock

import httpx
from streamlit.testing.v1 import AppTest

import ui


class TestStreamlitAPIClient(unittest.TestCase):
    def test_ui_renders_and_openai_requires_explicit_confirmation(self):
        app = AppTest.from_file(str(Path(ui.__file__))).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.selectbox), 1)

        app.selectbox[0].select("openai").run()
        self.assertTrue(app.button[0].disabled)
        app.checkbox[0].check().run()
        self.assertFalse(app.button[0].disabled)
        self.assertFalse(app.exception)

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
