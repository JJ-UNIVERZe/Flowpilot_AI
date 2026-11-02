"""Streamlit client for the FlowPilot HTTP API."""

import os
from typing import Any

import httpx
import streamlit as st


API_BASE_URL = os.getenv("FLOWPILOT_API_URL", "http://127.0.0.1:8000").rstrip("/")


class APIRequestError(RuntimeError):
    """A safe, customer-content-free error for API communication failures."""


def _request_api(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = httpx.request(
            method,
            f"{API_BASE_URL}{path}",
            json=payload,
            headers=headers,
            timeout=15.0,
        )
    except httpx.RequestError as error:
        raise APIRequestError("FlowPilot API is unavailable.") from error

    if not response.is_success:
        raise APIRequestError(
            f"FlowPilot API rejected the request (HTTP {response.status_code})."
        )

    try:
        return response.json()
    except ValueError as error:
        raise APIRequestError("FlowPilot API returned an invalid response.") from error


def _generate(message: str, mode: str) -> None:
    try:
        response = _request_api(
            "POST",
            "/workflow",
            {"message": message, "mode": mode},
        )
    except APIRequestError as error:
        st.error(str(error))
        return
    st.session_state["workflow_response"] = response


def _review(response_id: str, decision: str, reviewer_key: str) -> None:
    try:
        response = _request_api(
            "POST",
            f"/responses/{response_id}/review",
            {"decision": decision},
            {"X-Review-API-Key": reviewer_key},
        )
    except APIRequestError as error:
        st.error(str(error))
        return
    st.session_state["workflow_response"] = response


def main() -> None:
    st.title("FlowPilot AI")
    st.caption("Local support-response demo. Responses are never sent to customers.")

    mode = st.selectbox("Response mode", ("template", "mock", "openai"))
    message = st.text_area("Customer message", max_chars=4000)
    confirmed_live_call = False
    if mode == "openai":
        confirmed_live_call = st.checkbox(
            "I understand OpenAI mode may send a real, billable request."
        )

    if st.button(
        "Generate response",
        disabled=(mode == "openai" and not confirmed_live_call),
    ):
        if not message.strip():
            st.error("Enter a customer message first.")
        else:
            _generate(message, mode)

    current = st.session_state.get("workflow_response")
    if not current:
        return

    result = current["result"]
    st.subheader("Generated response")
    st.text(result["customer_response"])
    st.write(f"Approval status: **{result['approval_status']}**")

    if result["approval_status"] != "Pending":
        st.write(
            "Next workflow stage: "
            + ("allowed" if result["can_proceed"] else "blocked")
        )
        return

    reviewer_key = st.text_input("Reviewer key", type="password")
    approve_column, reject_column = st.columns(2)
    response_id = current["response_id"]
    if approve_column.button("Approve response"):
        if not reviewer_key:
            st.error("A reviewer key is required.")
        else:
            _review(response_id, "Approved", reviewer_key)
    if reject_column.button("Reject response"):
        if not reviewer_key:
            st.error("A reviewer key is required.")
        else:
            _review(response_id, "Rejected", reviewer_key)


if __name__ == "__main__":
    main()
