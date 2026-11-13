"""Strict, read-only order and refund tools for the agent workflow."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from datetime import date
from typing import Any

import database


ORDER_ID_RE = re.compile(r"ORD\d{4}")
CUSTOMER_ID_RE = re.compile(r"CUST\d{4}")

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_order",
            "description": "Look up a fictional order owned by the supplied customer.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^ORD[0-9]{4}$"},
                    "customer_id": {"type": "string", "pattern": "^CUST[0-9]{4}$"},
                },
                "required": ["order_id", "customer_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_refund_info",
            "description": "Check fictional refund eligibility and policy facts.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "order_id": {"type": "string", "pattern": "^ORD[0-9]{4}$"},
                },
                "required": ["order_id"],
                "additionalProperties": False,
            },
        },
    },
]


def _valid_id(value: Any, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _record_call(name: str, arguments: dict[str, Any], status: str, started: float) -> None:
    elapsed_ms = max(0.0, (time.perf_counter() - started) * 1000)
    database.create_database()
    try:
        with closing(sqlite3.connect(database.DATABASE_PATH)) as connection, connection:
            connection.execute(
                """
                INSERT INTO tool_call_logs
                    (tool_name, arguments_json, result_status, duration_ms)
                VALUES (?, ?, ?, ?)
                """,
                (name, json.dumps(arguments, sort_keys=True, default=str), status, elapsed_ms),
            )
    except sqlite3.Error as error:
        raise database.DatabaseError(database.DATABASE_UNAVAILABLE_TEXT) from error


def _finish_call(
    name: str, arguments: dict[str, Any], result: dict[str, Any], started: float
) -> dict[str, Any]:
    try:
        _record_call(name, arguments, result["status"], started)
    except database.DatabaseError:
        return {
            "ok": False,
            "status": "logging_error",
            "error": "The tool call could not be recorded.",
        }
    return result


def record_tool_rejection(
    name: str, arguments: dict[str, Any], status: str
) -> dict[str, Any]:
    """Persist a rejected model tool request without dispatching it."""
    try:
        _record_call(name, arguments, status, time.perf_counter())
    except database.DatabaseError:
        return {
            "ok": False,
            "status": "logging_error",
            "error": "The tool call could not be recorded.",
        }
    return {"ok": False, "status": status, "error": "Tool request was rejected."}


def lookup_order(order_id: str, customer_id: str) -> dict[str, Any]:
    """Return order facts only when the order belongs to ``customer_id``."""
    started = time.perf_counter()
    args = {"order_id": order_id, "customer_id": customer_id}
    if not _valid_id(order_id, ORDER_ID_RE) or not _valid_id(customer_id, CUSTOMER_ID_RE):
        result = {"ok": False, "status": "invalid_arguments", "error": "Invalid order or customer ID."}
    else:
        try:
            database.create_database()
            with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
                connection.row_factory = sqlite3.Row
                row = connection.execute(
                    """
                    SELECT order_id, customer_id, items, amount, order_status,
                           order_date, delivery_date
                    FROM orders WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()
            if row is None:
                result = {"ok": False, "status": "not_found", "error": "Order not found."}
            elif row["customer_id"] != customer_id:
                result = {
                    "ok": False,
                    "status": "customer_mismatch",
                    "error": "Order does not belong to this customer.",
                }
            else:
                result = {
                    "ok": True,
                    "status": "found",
                    "order": {
                        "order_id": row["order_id"],
                        "customer_id": row["customer_id"],
                        "items": json.loads(row["items"] or "[]"),
                        "amount": float(row["amount"] or 0),
                        "status": row["order_status"],
                        "order_date": row["order_date"],
                        "delivery_date": row["delivery_date"],
                    },
                }
        except (database.DatabaseError, sqlite3.Error, ValueError, TypeError):
            result = {
                "ok": False,
                "status": "database_error",
                "error": "Order information is temporarily unavailable.",
            }
    return _finish_call("lookup_order", args, result, started)


def _refund_policy() -> tuple[int, set[str]]:
    try:
        window_days = int(os.getenv("REFUND_WINDOW_DAYS", "30"))
    except ValueError as error:
        raise ValueError("REFUND_WINDOW_DAYS must be a positive integer.") from error
    if window_days < 1:
        raise ValueError("REFUND_WINDOW_DAYS must be a positive integer.")
    categories = {
        category.strip().casefold()
        for category in os.getenv("NON_REFUNDABLE_CATEGORIES", "digital,final_sale").split(",")
        if category.strip()
    }
    return window_days, categories


def get_refund_info(order_id: str) -> dict[str, Any]:
    """Return refund eligibility derived from the order and configured policy."""
    started = time.perf_counter()
    args = {"order_id": order_id}
    if not _valid_id(order_id, ORDER_ID_RE):
        result = {"ok": False, "status": "invalid_arguments", "error": "Invalid order ID."}
    else:
        try:
            window_days, non_refundable = _refund_policy()
            database.create_database()
            with closing(sqlite3.connect(database.DATABASE_PATH)) as connection:
                connection.row_factory = sqlite3.Row
                order = connection.execute(
                    """
                    SELECT order_id, items, amount, order_status, delivery_date,
                           refund_status, refund_amount
                    FROM orders WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()
                refund = connection.execute(
                    "SELECT status, amount FROM refunds WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
            if order is None:
                result = {"ok": False, "status": "not_found", "error": "Order not found."}
            else:
                items = json.loads(order["items"] or "[]")
                item_categories = {
                    str(item.get("category", "")).casefold()
                    for item in items
                    if isinstance(item, dict)
                }
                order_refund_status = str(order["refund_status"]).casefold()
                if refund is not None or order_refund_status in {
                    "refund requested", "refund processing", "refund completed"
                }:
                    eligible = False
                    reason = "A refund request already exists for this order."
                elif item_categories & non_refundable:
                    eligible = False
                    reason = "One or more items are in a non-refundable category."
                elif order["order_status"].casefold() != "delivered":
                    eligible = False
                    reason = "The order must be delivered before refund eligibility can be checked."
                else:
                    delivered = date.fromisoformat(order["delivery_date"])
                    as_of_date = date.fromisoformat(
                        os.getenv("REFUND_AS_OF_DATE") or date.today().isoformat()
                    )
                    elapsed_days = (as_of_date - delivered).days
                    if elapsed_days < 0:
                        eligible = False
                        reason = "The delivery date is in the future; eligibility needs review."
                    elif elapsed_days > window_days:
                        eligible = False
                        reason = f"The {window_days}-day refund window has passed."
                    else:
                        eligible = True
                        reason = f"Within the {window_days}-day refund window."
                result = {
                    "ok": True,
                    "status": "eligible" if eligible else "ineligible",
                    "order_id": order_id,
                    "eligible": eligible,
                    "window_days": window_days,
                    "reason": reason,
                    "refundable_amount": float(order["amount"]) if eligible else 0.0,
                    "existing_refund_status": refund["status"] if refund else None,
                    "existing_refund_amount": float(refund["amount"]) if refund else None,
                }
        except ValueError as error:
            result = {"ok": False, "status": "policy_error", "error": str(error)}
        except (database.DatabaseError, sqlite3.Error, TypeError, json.JSONDecodeError):
            result = {
                "ok": False,
                "status": "database_error",
                "error": "Refund information is temporarily unavailable.",
            }
    return _finish_call("get_refund_info", args, result, started)


def execute_tool_call(name: str, arguments_json: str) -> dict[str, Any]:
    """Parse and strictly validate OpenAI tool arguments before dispatch."""
    started = time.perf_counter()
    try:
        arguments = json.loads(arguments_json)
    except (TypeError, json.JSONDecodeError):
        return _finish_call(
            name,
            {"raw_arguments": str(arguments_json)[:1000]},
            {"ok": False, "status": "invalid_arguments", "error": "Arguments must be valid JSON."},
            started,
        )
    if not isinstance(arguments, dict):
        return _finish_call(
            name,
            {"raw_arguments": str(arguments_json)[:1000]},
            {"ok": False, "status": "invalid_arguments", "error": "Arguments must be a JSON object."},
            started,
        )
    allowed = {
        "lookup_order": {"order_id", "customer_id"},
        "get_refund_info": {"order_id"},
    }
    if name not in allowed:
        return _finish_call(
            name,
            arguments,
            {"ok": False, "status": "unknown_tool", "error": "Tool is not available."},
            started,
        )
    if set(arguments) != allowed[name]:
        return _finish_call(
            name,
            arguments,
            {
                "ok": False,
                "status": "invalid_arguments",
                "error": "Unexpected or missing tool arguments.",
            },
            started,
        )
    if name == "lookup_order":
        return lookup_order(arguments["order_id"], arguments["customer_id"])
    return get_refund_info(arguments["order_id"])
