"""Streamlit client for the FlowPilot HTTP API."""

import os
from typing import Any

import httpx
import pandas as pd
import streamlit as st


API_BASE_URL = os.getenv("FLOWPILOT_API_URL", "http://127.0.0.1:8000").rstrip("/")


class APIRequestError(RuntimeError):
    """A safe, customer-content-free error for API communication failures."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


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
            f"FlowPilot API rejected the request (HTTP {response.status_code}).",
            status_code=response.status_code,
        )

    try:
        return response.json()
    except ValueError as error:
        raise APIRequestError("FlowPilot API returned an invalid response.") from error


def _friendly_api_error(error: APIRequestError, resource: str) -> str:
    if error.status_code in (401, 403):
        return "Enter a valid reviewer key in the sidebar to access dashboard data."
    if error.status_code == 404:
        if resource == "evaluation":
            return "Evaluation results are not available yet. Run scripts/evaluate.py first."
        return "Review statistics are not available."
    if error.status_code == 429:
        return "The FlowPilot API is receiving too many requests. Try again shortly."
    if error.status_code == 503:
        return "The FlowPilot API is temporarily unavailable. Try again later."
    if error.status_code is None:
        return "FlowPilot API is unreachable. Check that the API is running."
    return f"Could not load {resource} data (HTTP {error.status_code})."


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


def _render_evaluation(evaluation: dict[str, Any]) -> None:
    metrics = evaluation.get("metrics")
    if not isinstance(metrics, dict):
        st.error("Evaluation data has an unexpected format.")
        return

    metric_fields = (
        ("classification_accuracy", "Classification accuracy", ".1%", ""),
        ("order_id_extraction_accuracy", "Order-ID accuracy", ".1%", ""),
        ("outcome_accuracy", "Outcome accuracy", ".1%", ""),
        ("safety_pass_rate", "Safety pass rate", ".1%", ""),
        ("mean_processing_time_ms", "Mean processing time", ".2f", " ms"),
        ("p95_processing_time_ms", "p95 processing time", ".2f", " ms"),
    )
    for offset in range(0, len(metric_fields), 3):
        columns = st.columns(3)
        for column, (key, label, value_format, suffix) in zip(
            columns, metric_fields[offset : offset + 3]
        ):
            value = metrics.get(key)
            if not isinstance(value, (int, float)):
                st.error("Evaluation metrics have an unexpected format.")
                return
            with column:
                st.metric(label, f"{value:{value_format}}{suffix}")

    st.caption(
        f"Last run: {evaluation.get('timestamp_utc', 'Unknown')} · "
        f"Mode: {evaluation.get('mode', 'Unknown')} · "
        f"Cases: {evaluation.get('case_count', 'Unknown')}"
    )
    st.info(
        f"Measured on {evaluation.get('case_count', 'N')} synthetic cases in "
        "template/mock mode. Not live OpenAI."
    )
    st.write("Refresh the evaluation by running:")
    st.code(r"python scripts\evaluate.py", language="powershell")

    accuracy_by_tag = metrics.get("accuracy_per_tag", {})
    if not isinstance(accuracy_by_tag, dict):
        st.error("Evaluation tag metrics have an unexpected format.")
        return
    tag_frame = pd.DataFrame(
        [
            {"Tag": tag, "Classification accuracy": stats["classification_accuracy"]}
            for tag, stats in accuracy_by_tag.items()
            if isinstance(stats, dict) and "classification_accuracy" in stats
        ]
    )
    st.subheader("Accuracy by tag")
    if tag_frame.empty:
        st.info("No tag accuracy data is available.")
    else:
        st.bar_chart(
            tag_frame,
            x="Tag",
            y="Classification accuracy",
            alt="Classification accuracy for each evaluation tag",
        )

    confusion_matrix = metrics.get("confusion_matrix", {})
    st.subheader("Confusion matrix")
    if not isinstance(confusion_matrix, dict) or not confusion_matrix:
        st.info("No confusion matrix data is available.")
    else:
        matrix_frame = pd.DataFrame.from_dict(confusion_matrix, orient="index")
        matrix_frame.index.name = "Expected category"
        st.dataframe(
            matrix_frame,
            alt="Expected and predicted categories for evaluated cases",
        )

    failures = evaluation.get("failures", [])
    st.subheader("Failed cases")
    if not isinstance(failures, list):
        st.error("Failed-case data has an unexpected format.")
        return
    failed_categories = sorted(
        {
            category
            for failure in failures
            if isinstance(failure, dict)
            for group in (failure.get("expected"), failure.get("actual"))
            if isinstance(group, dict)
            if isinstance((category := group.get("category")), str)
        }
    )
    selected_category = st.selectbox(
        "Filter failed cases by category",
        ["All categories", *failed_categories],
        key="evaluation_category_filter",
    )
    displayed_failures = []
    for failure in failures:
        if not isinstance(failure, dict):
            continue
        expected = failure.get("expected", {})
        actual = failure.get("actual", {})
        if not isinstance(expected, dict) or not isinstance(actual, dict):
            continue
        if selected_category != "All categories" and selected_category not in {
            expected.get("category"),
            actual.get("category"),
        }:
            continue
        displayed_failures.append(
            {
                "Case": failure.get("id"),
                "Expected category": expected.get("category"),
                "Actual category": actual.get("category"),
                "Expected order ID": expected.get("order_id"),
                "Actual order ID": actual.get("order_id"),
                "Expected outcome": expected.get("outcome"),
                "Actual outcome": actual.get("outcome"),
                "Safety passed": failure.get("safety_pass"),
            }
        )
    st.dataframe(
        pd.DataFrame(
            displayed_failures,
            columns=[
                "Case",
                "Expected category",
                "Actual category",
                "Expected order ID",
                "Actual order ID",
                "Expected outcome",
                "Actual outcome",
                "Safety passed",
            ],
        ),
        hide_index=True,
        alt="Failed evaluation cases and expected versus actual results",
    )


def _render_review_stats(stats: dict[str, Any]) -> None:
    by_decision = stats.get("by_decision", {})
    by_category = stats.get("by_category", {})
    total = stats.get("total_decisions")
    if (
        not isinstance(total, int)
        or not isinstance(by_decision, dict)
        or not isinstance(by_category, dict)
    ):
        st.error("Review statistics have an unexpected format.")
        return

    st.metric("Total decisions", total)
    st.subheader("Decisions")
    decision_frame = pd.DataFrame(
        [
            {"Decision": decision, "Count": by_decision.get(decision, 0)}
            for decision in ("Approved", "Rejected")
        ]
    )
    st.bar_chart(
        decision_frame,
        x="Decision",
        y="Count",
        alt="Recorded review decisions, approved compared with rejected",
    )

    st.subheader("Decisions by category")
    if not by_category:
        st.info("The audit table does not record decision categories.")
        return
    category_frame = pd.DataFrame(
        [{"Category": category, "Count": count} for category, count in by_category.items()]
    )
    st.bar_chart(
        category_frame,
        x="Category",
        y="Count",
        alt="Recorded review decisions grouped by request category",
    )


def main() -> None:
    st.title("FlowPilot AI")
    st.caption("Local support-response demo. Responses are never sent to customers.")

    with st.sidebar:
        st.subheader("API access")
        st.caption(f"API: {API_BASE_URL}")
        reviewer_key = st.text_input("Reviewer key", type="password")

    workflow_tab, evaluation_tab, review_stats_tab = st.tabs(
        ["Workflow", "Evaluation", "Review stats"]
    )
    with workflow_tab:
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
        if current:
            result = current["result"]
            st.subheader("Generated response")
            st.text(result["customer_response"])
            st.badge(
                "Policy found" if result.get("policy_found") else "No policy found",
                color="green" if result.get("policy_found") else "gray",
            )
            with st.expander("Sources used"):
                sources = result.get("policy_sources", [])
                if isinstance(sources, list) and sources:
                    for source in sources:
                        if isinstance(source, dict) and source.get("title"):
                            st.write(source["title"])
                else:
                    st.caption("No policy passages were retrieved.")
            st.write(f"Approval status: **{result['approval_status']}**")

            if result["approval_status"] != "Pending":
                st.write(
                    "Next workflow stage: "
                    + ("allowed" if result["can_proceed"] else "blocked")
                )
            else:
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

    dashboard_headers = {"X-Review-API-Key": reviewer_key}
    with evaluation_tab:
        try:
            evaluation = _request_api(
                "GET", "/evaluation", headers=dashboard_headers
            )
        except APIRequestError as error:
            st.error(_friendly_api_error(error, "evaluation"))
        else:
            if not isinstance(evaluation, dict):
                st.error("Evaluation data has an unexpected format.")
            else:
                _render_evaluation(evaluation)

    with review_stats_tab:
        try:
            review_stats = _request_api("GET", "/stats", headers=dashboard_headers)
        except APIRequestError as error:
            st.error(_friendly_api_error(error, "review statistics"))
        else:
            if not isinstance(review_stats, dict):
                st.error("Review statistics have an unexpected format.")
            else:
                _render_review_stats(review_stats)


if __name__ == "__main__":
    main()
