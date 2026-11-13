"""Cost estimates and SQLite persistence for agent-run measurements."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import closing
from typing import Any

import database


def _price_per_million(name: str) -> float | None:
    value = os.getenv(name, "").strip()
    if not value:
        return None
    try:
        price = float(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a non-negative number.") from error
    if price < 0:
        raise ValueError(f"{name} must be a non-negative number.")
    return price


def estimate_cost_usd(
    prompt_tokens: int, completion_tokens: int, model_name: str, mode: str
) -> float | None:
    """Estimate cost using environment-supplied USD prices per million tokens.

    Mock runs are always zero-cost. Live cost stays unknown until both model
    price variables are configured; no price is embedded in the code.
    """
    if mode == "mock":
        return 0.0
    if mode != "live":
        raise ValueError("mode must be 'mock' or 'live'.")
    if prompt_tokens < 0 or completion_tokens < 0:
        raise ValueError("Token counts cannot be negative.")
    input_price = _price_per_million("OPENAI_PRICE_INPUT_PER_1M")
    output_price = _price_per_million("OPENAI_PRICE_OUTPUT_PER_1M")
    if input_price is None or output_price is None:
        return None
    return round(
        prompt_tokens * input_price / 1_000_000
        + completion_tokens * output_price / 1_000_000,
        10,
    )


def record_run_metrics(
    run_id: str,
    metrics: dict[str, Any],
    mode: str,
    model_name: str,
    total_latency_ms: float,
    approval_wait_ms: float = 0.0,
) -> None:
    """Insert or update one run's measurements without storing prompt text."""
    if not run_id or mode not in {"mock", "live"}:
        raise ValueError("run_id and a supported mode are required.")
    prompt_tokens = int(metrics.get("prompt_tokens", 0))
    completion_tokens = int(metrics.get("completion_tokens", 0))
    tool_call_count = int(metrics.get("tool_call_count", 0))
    llm_latency_ms = float(metrics.get("llm_latency_ms", 0.0))
    if min(prompt_tokens, completion_tokens, tool_call_count) < 0:
        raise ValueError("Metric counts cannot be negative.")
    if min(total_latency_ms, approval_wait_ms, llm_latency_ms) < 0:
        raise ValueError("Metric durations cannot be negative.")
    estimated_cost = estimate_cost_usd(
        prompt_tokens, completion_tokens, model_name, mode
    )
    node_latencies = metrics.get("node_latency_ms", {})
    if not isinstance(node_latencies, dict):
        raise ValueError("node_latency_ms must be an object.")
    database.create_database()
    try:
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection, connection:
            connection.execute(
                """
                INSERT INTO run_metrics (
                    run_id, mode, model_name, total_latency_ms,
                    approval_wait_ms, node_latencies_json, llm_latency_ms, prompt_tokens,
                    completion_tokens, tool_call_count, estimated_cost_usd,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    mode = excluded.mode,
                    model_name = excluded.model_name,
                    total_latency_ms = excluded.total_latency_ms,
                    approval_wait_ms = excluded.approval_wait_ms,
                    node_latencies_json = excluded.node_latencies_json,
                    llm_latency_ms = excluded.llm_latency_ms,
                    prompt_tokens = excluded.prompt_tokens,
                    completion_tokens = excluded.completion_tokens,
                    tool_call_count = excluded.tool_call_count,
                    estimated_cost_usd = excluded.estimated_cost_usd,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    run_id,
                    mode,
                    model_name,
                    total_latency_ms,
                    approval_wait_ms,
                    json.dumps(node_latencies, sort_keys=True),
                    llm_latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    tool_call_count,
                    estimated_cost,
                ),
            )
    except sqlite3.Error as error:
        raise database.DatabaseError(database.DATABASE_UNAVAILABLE_TEXT) from error


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(ordered[index], 3)


def get_metrics_summary() -> dict[str, Any]:
    """Return measured run counts, latency percentiles, usage, and costs."""
    database.create_database()
    try:
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
            rows = connection.execute(
                """
                SELECT total_latency_ms, prompt_tokens, completion_tokens,
                       estimated_cost_usd, approval_wait_ms
                FROM run_metrics ORDER BY total_latency_ms
                """
            ).fetchall()
    except sqlite3.Error as error:
        raise database.DatabaseError(database.DATABASE_UNAVAILABLE_TEXT) from error
    latencies = [float(row[0]) for row in rows]
    return {
        "count": len(rows),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 3) if rows else 0.0,
        "p50_latency_ms": _percentile(latencies, 0.50),
        "p95_latency_ms": _percentile(latencies, 0.95),
        "avg_approval_wait_ms": round(
            sum(float(row[4]) for row in rows) / len(rows), 3
        ) if rows else 0.0,
        "total_prompt_tokens": sum(int(row[1]) for row in rows),
        "total_completion_tokens": sum(int(row[2]) for row in rows),
        "total_tokens": sum(int(row[1]) + int(row[2]) for row in rows),
        "total_cost_usd": round(
            sum(float(row[3]) for row in rows if row[3] is not None), 10
        ),
        "cost_estimates_available": sum(row[3] is not None for row in rows),
    }
