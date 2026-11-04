"""Local HTTP API for generating responses and recording human review."""

import hmac
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from threading import RLock
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

import approval
import database
import flowpilot


app = FastAPI(title="FlowPilot AI", version="1.0")
pending_responses: dict[str, dict[str, Any]] = {}
_pending_lock = RLock()
DEFAULT_EVALUATION_RESULTS_PATH = Path(__file__).with_name("evals") / "results.json"


class WorkflowRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    mode: Literal["template", "mock", "openai"] = "template"


class ReviewRequest(BaseModel):
    decision: Literal["Approved", "Rejected"]


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
