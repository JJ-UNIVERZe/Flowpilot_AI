"""Local HTTP API for generating responses and recording human review."""

import hmac
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from langgraph.types import Command

import approval
import agent_graph
import database
import flowpilot
import knowledge_base
import metrics as run_metrics


app = FastAPI(title="FlowPilot AI", version="1.0")
pending_responses: dict[str, dict[str, Any]] = {}
_pending_lock = RLock()
DEFAULT_EVALUATION_RESULTS_PATH = Path(__file__).with_name("evals") / "results.json"


class WorkflowRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["template", "mock", "openai"] = "template"


class ReviewRequest(BaseModel):
    decision: Literal["Approved", "Rejected"]


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    customer_id: str = Field(pattern=r"^CUST[0-9]{4}$")
    mode: Literal["mock", "live"] = "mock"


class AgentApprovalRequest(BaseModel):
    action: Literal["approve", "edit"] = "approve"
    draft_reply: str | None = Field(default=None, max_length=4000)


agent_runs: dict[str, dict[str, Any]] = {}
_agent_runs_lock = RLock()


def _select_provider(mode: str):
    if mode == "template":
        return None

    if not flowpilot._LLM_MODULE_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The selected response provider is unavailable.",
        )

    if mode == "openai":
        return flowpilot.get_openai_provider() if flowpilot.get_openai_provider else None

    from llm_response_generator import MockLLM

    response = "I can help with your request."
    return MockLLM({
        "Delivery Issue": response,
        "Refund Inquiry": response,
        "Payment Issue": response,
        "General Inquiry": response,
        "Unknown Request": response,
    })


def _select_agent_provider(mode: str):
    if mode == "mock":
        from llm_response_generator import MockLLM

        response = "I can help with your request. A human reviewer will verify any decision."
        return MockLLM(
            {
                category: response
                for category in (
                    "Delivery Issue",
                    "Refund Inquiry",
                    "Payment Issue",
                    "General Inquiry",
                    "Damaged Item",
                    "Cancellation",
                    "Address Change",
                    "Unknown Request",
                )
            }
        )
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live mode requires OPENAI_API_KEY.",
        )
    if not flowpilot.get_openai_provider:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The OpenAI provider is unavailable.",
        )
    provider = flowpilot.get_openai_provider()
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The OpenAI provider is unavailable.",
        )
    return provider


def _agent_run_payload(run_id: str, state: dict[str, Any]) -> dict[str, Any]:
    interrupts = state.get("__interrupt__", ())
    interrupt_value = interrupts[0].value if interrupts else None
    status_text = "awaiting_approval" if interrupts else state.get("status", "running")
    return {
        "run_id": run_id,
        "status": status_text,
        "category": state.get("category"),
        "draft_reply": state.get("draft_reply", ""),
        "tool_results": state.get("tool_results", []),
        "validation_flags": state.get("validation_flags", []),
        "confidence": state.get("confidence", 0.0),
        "approval_status": state.get("approval_status", "Pending"),
        "can_proceed": state.get("can_proceed", False),
        "metrics": state.get("metrics", {}),
        "errors": state.get("errors", []),
        "interrupt": interrupt_value,
    }


def _persist_agent_metrics(
    run_id: str, run: dict[str, Any], state: dict[str, Any]
) -> None:
    metrics_data = dict(state.get("metrics", {}))
    metrics_data["total_latency_ms"] = round(
        max(0.0, (time.perf_counter() - run["started_perf_counter"]) * 1000), 3
    )
    state["metrics"] = metrics_data
    model_name = str(metrics_data.get("model_name", "unknown"))
    try:
        run_metrics.record_run_metrics(
            run_id=run_id,
            metrics=metrics_data,
            mode=run["mode"],
            model_name=model_name,
            total_latency_ms=metrics_data["total_latency_ms"],
        )
    except database.DatabaseError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=database.DATABASE_UNAVAILABLE_TEXT,
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent metrics configuration is invalid.",
        ) from error


def _resume_agent_run(
    run_id: str, run: dict[str, Any], decision: str, draft_reply: str | None = None
) -> dict[str, Any]:
    state = run["state"]
    if not state.get("__interrupt__"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent run is not waiting for a reviewer decision.",
        )
    if decision in {"approve", "reject"}:
        try:
            approval.record_approval(
                response=state.get("draft_reply", ""),
                decision="Approved" if decision == "approve" else "Rejected",
                customer_message=state.get("message", ""),
            )
        except database.DatabaseError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=database.DATABASE_UNAVAILABLE_TEXT,
            ) from error
    config = {"configurable": {"thread_id": run_id}}
    resumed = run["graph"].invoke(
        Command(
            resume={
                "decision": decision,
                "draft_reply": draft_reply,
            }
        ),
        config=config,
    )
    run["state"] = resumed
    _persist_agent_metrics(run_id, run, resumed)
    return _agent_run_payload(run_id, resumed)


def _require_reviewer_key(supplied_key: str | None) -> None:
    expected_key = os.getenv("REVIEW_API_KEY", "")
    if not expected_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Human review is not configured.",
        )
    if supplied_key is None or not hmac.compare_digest(supplied_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid reviewer key is required.",
        )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/agent/run", status_code=status.HTTP_201_CREATED)
def run_agent(request: AgentRunRequest) -> dict[str, Any]:
    """Run the tool-using LangGraph agent and pause for human approval."""
    provider = _select_agent_provider(request.mode)
    run_id = uuid4().hex
    graph = agent_graph.build_agent_graph(provider)
    config = {"configurable": {"thread_id": run_id}}
    started = time.perf_counter()
    state = graph.invoke(
        agent_graph.initial_state(
            request.message, request.customer_id, run_id, request.mode
        ),
        config=config,
    )
    run = {
        "graph": graph,
        "state": state,
        "mode": request.mode,
        "started_perf_counter": started,
    }
    with _agent_runs_lock:
        agent_runs[run_id] = run
    _persist_agent_metrics(run_id, run, state)
    return _agent_run_payload(run_id, state)


@app.get("/agent/runs/{run_id}")
def get_agent_run(
    run_id: str,
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Return a run's draft and tool facts to an authenticated reviewer."""
    _require_reviewer_key(x_review_api_key)
    with _agent_runs_lock:
        run = agent_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run was not found.")
        return _agent_run_payload(run_id, run["state"])


@app.post("/agent/runs/{run_id}/approve")
def approve_agent_run(
    run_id: str,
    request: AgentApprovalRequest,
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Approve a draft or submit an edited draft for revalidation."""
    _require_reviewer_key(x_review_api_key)
    if request.action == "edit" and not request.draft_reply:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="An edited draft is required for the edit action.",
        )
    with _agent_runs_lock:
        run = agent_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run was not found.")
        return _resume_agent_run(
            run_id, run, request.action, request.draft_reply
        )


@app.post("/agent/runs/{run_id}/reject")
def reject_agent_run(
    run_id: str,
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Reject a draft and persist the reviewer decision."""
    _require_reviewer_key(x_review_api_key)
    with _agent_runs_lock:
        run = agent_runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Agent run was not found.")
        return _resume_agent_run(run_id, run, "reject")


@app.get("/metrics/summary")
def get_metrics_summary(
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Return aggregate agent metrics to an authenticated reviewer."""
    _require_reviewer_key(x_review_api_key)
    try:
        return run_metrics.get_metrics_summary()
    except database.DatabaseError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent metrics are unavailable.",
        ) from error


@app.get("/kb/search")
def search_policy_knowledge_base(
    q: str = Query(min_length=1, max_length=200),
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Return read-only policy matches to an authenticated reviewer."""
    _require_reviewer_key(x_review_api_key)
    if not q.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Query must not be empty.",
        )
    passages = knowledge_base.search(q, k=3)
    return {
        "results": [
            {
                "doc_id": passage.doc_id,
                "title": passage.title,
                "text": passage.text,
                "score": passage.score,
            }
            for passage in passages
        ]
    }


@app.get("/evaluation")
def get_evaluation(
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> Any:
    """Return the latest offline evaluation report without modifying it."""
    _require_reviewer_key(x_review_api_key)
    results_path = Path(
        os.getenv("EVALUATION_RESULTS_PATH", str(DEFAULT_EVALUATION_RESULTS_PATH))
    )
    try:
        with results_path.open(encoding="utf-8") as results_file:
            return json.load(results_file)
    except FileNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Evaluation results not found. Run scripts/evaluate.py first.",
        ) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Evaluation results could not be read.",
        ) from error


@app.get("/stats")
def get_review_stats(
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Return read-only counts of recorded review decisions."""
    _require_reviewer_key(x_review_api_key)
    database_uri = database.DATABASE_PATH.resolve().as_uri() + "?mode=ro"
    try:
        with closing(sqlite3.connect(database_uri, uri=True)) as connection:
            columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(approval_logs)"
                ).fetchall()
            }
            if not columns:
                raise sqlite3.OperationalError("approval audit table is unavailable")

            decision_rows = connection.execute(
                """
                SELECT approval_status, COUNT(*)
                FROM approval_logs
                WHERE approval_status IN (?, ?)
                GROUP BY approval_status
                """,
                ("Approved", "Rejected"),
            ).fetchall()
            by_decision = {"Approved": 0, "Rejected": 0}
            by_decision.update(
                {decision: count for decision, count in decision_rows}
            )

            by_category: dict[str, int] = {}
            if "category" in columns:
                category_rows = connection.execute(
                    """
                    SELECT category, COUNT(*)
                    FROM approval_logs
                    WHERE approval_status IN (?, ?) AND category IS NOT NULL
                    GROUP BY category
                    """,
                    ("Approved", "Rejected"),
                ).fetchall()
                by_category = {category: count for category, count in category_rows}
    except sqlite3.Error as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Review statistics are unavailable.",
        ) from error

    return {
        "total_decisions": sum(by_decision.values()),
        "by_decision": by_decision,
        "by_category": by_category,
    }


@app.post("/process", status_code=status.HTTP_201_CREATED)
@app.post("/workflow", status_code=status.HTTP_201_CREATED)
def create_workflow(request: WorkflowRequest) -> dict[str, Any]:
    """Generate a response but leave it Pending until an authorized human reviews it."""
    provider = _select_provider(request.mode)
    result = flowpilot.run_workflow_with_provider(request.message, provider)
    response_id = uuid4().hex

    result["approval_status"] = approval.PENDING
    result["approval"] = None
    result["can_proceed"] = False
    with _pending_lock:
        pending_responses[response_id] = result

    return {
        "response_id": response_id,
        "mode": request.mode,
        "policy_found": result["policy_found"],
        "policy_sources": result["policy_sources"],
        "result": result,
    }


@app.post("/responses/{response_id}/review")
def review_workflow_response(
    response_id: str,
    request: ReviewRequest,
    x_review_api_key: str | None = Header(default=None, alias="X-Review-API-Key"),
) -> dict[str, Any]:
    """Record an explicit, authenticated human approval or rejection."""
    _require_reviewer_key(x_review_api_key)

    with _pending_lock:
        result = pending_responses.get(response_id)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Pending response was not found.",
            )

        try:
            record = approval.record_approval(
                response=result["customer_response"],
                decision=request.decision,
                customer_message=result["original_message"],
            )
        except database.DatabaseError as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=database.DATABASE_UNAVAILABLE_TEXT,
            ) from error

        result["approval"] = record
        result["approval_status"] = request.decision
        result["can_proceed"] = approval.can_proceed(request.decision)
        del pending_responses[response_id]

    return {"response_id": response_id, "result": result}
