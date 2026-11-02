"""
FlowPilot AI — Stages 1–8: Request Classification, Order ID Extraction,
Local Sample Order Lookup, Refund/Payment Status Lookup, Customer Response
Generation, Organized Templates with a Priority-Ordered Classifier, Graceful
Error Handling and JSON Export, plus Human-in-the-Loop Approval.

The workflow accepts a message, classifies it, extracts an order ID, looks
up delivery, refund, or payment details in a local SQLite sample database,
and turns the result into a customer-friendly response. The Stage 6 milestone
adds a human review step: the generated response is shown to a reviewer, who
must approve or reject it, and the decision is written to an audit log.

Nothing is ever sent, refunded, paid, or changed in the orders table. A
response is never approved automatically.

Run it:
    python flowpilot.py                        # built-in test cases (no review)
    python flowpilot.py "My order ORD1042 has not arrived"
                                               # full workflow + human review
    python flowpilot.py --json "My order ORD1042 has not arrived"
                                               # review, then workflow_result.json
    python flowpilot.py --classify-tests       # classification test table
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import approval
import database
from database import (
    DATABASE_UNAVAILABLE_TEXT,
    DatabaseError,
    create_database,
    get_order,
    get_payment_status,
    get_refund_status,
)
from response_generator import generate_response

if TYPE_CHECKING:
    from llm_response_generator import LLMProvider

try:
    from llm_response_generator import (
        get_openai_provider,
    )
    _LLM_MODULE_AVAILABLE = True
except ImportError:
    _LLM_MODULE_AVAILABLE = False
    get_openai_provider = None  # type: ignore

# ---------------------------------------------------------------------------
# NODE 1 — Input
# ---------------------------------------------------------------------------
# Purpose: accept a customer message as a string.
# In a visual tool this is the "manual trigger" node you type into.
def input_node(message: str) -> str:
    return message


# ---------------------------------------------------------------------------
# NODE 2 — Normalize (lowercase)
# ---------------------------------------------------------------------------
# Purpose: convert the message to lowercase so "Order" and "order" are
# treated the same by the classifier. The original text is kept untouched
# so we can still show it at the end.
def normalize_node(message: str) -> str:
    return message.lower()


# ---------------------------------------------------------------------------
# INPUT VALIDATION AND SAFE ACTION LABELS  (NEW in Stage 7)
# ---------------------------------------------------------------------------
# Action labels and error text shown to the user. None of them contain SQL,
# file paths, stack traces, or raw database messages.
ACTION_ASK_FOR_MESSAGE = "Ask the customer to describe their request"
ACTION_ASK_FOR_ORDER_ID = "Ask the customer for their order ID"
ACTION_ASK_FOR_VALID_ORDER_ID = "Ask for a correctly formatted order ID"
ACTION_DATABASE_UNAVAILABLE = "Could not access the sample order database"
ACTION_UNEXPECTED_ERROR = "Handle the request manually"


def validate_message(message: str | None) -> str | None:
    """Return an issue code when a message cannot be processed, else None.

    The only issue code today is "invalid_message", which covers:
        - None, an empty string, or only spaces/tabs,
        - text with no letters at all (for example "12345"), which cannot
          describe a request.
    """
    if not isinstance(message, str) or not message.strip():
        return "invalid_message"
    if not any(character.isalpha() for character in message):
        return "invalid_message"
    return None


# ---------------------------------------------------------------------------
# NODE 3 — Classify (rule-based, priority order)
# ---------------------------------------------------------------------------
# Purpose: apply simple keyword rules, top to bottom, and decide both the
# category and the next action. The FIRST rule ("tier") that matches wins, so
# the order of the rules IS the priority list — this is our
# "if / else if / else" chain.
#
# HOW PRIORITY WORKS
# ------------------
# 1. Every tier is checked from top to bottom.
# 2. The first tier containing ANY matching keyword decides the result; the
#    lower tiers are then never consulted. Keywords inside one tier have no
#    priority over each other, because they all mean the same thing.
# 3. So a message with MULTIPLE keywords is resolved by "which tier is
#    highest?", not by "which word appears first in the sentence".
#    Example: "My order has not arrived, I want a refund" contains delivery
#    words AND refund words. The refund tier is above the delivery tier, so
#    the answer is Refund Inquiry.
#
# PRIORITY (highest first), and why:
#   Tier 1  Refund Inquiry   — the most specific money-back complaint. The
#                              customer may also say "order", "payment" or
#                              "not arrived", so refund must win over them.
#                              E.g. "I want a refund for order ORD1042"
#                              -> Refund Inquiry (not Delivery Issue).
#   Tier 2  Payment Issue    — also about money, but about the transaction
#                              rather than the parcel (declined, pending,
#                              charged). It beats delivery wording.
#                              E.g. "My payment for ORD5734 is pending"
#                              -> Payment Issue.
#   Tier 3  General Inquiry  — return-policy / return / exchange questions.
#                              These are policy questions, so they are not a
#                              delivery problem even when an order is named.
#                              E.g. "I want to return my order ORD1042"
#                              -> General Inquiry (this demo has no separate
#                              "Return Inquiry" category).
#   Tier 4  Delivery Issue   — a real delivery complaint (not arrived, late,
#                              tracking, parcel...). This is the normal
#                              delivery path.
#   Tier 5  Delivery Issue   — order-STATUS fallback: a bare mention of
#                              "order" with no delivery, money, or return
#                              word. This demo has no separate "Order Status
#                              Inquiry" category, so a status question is
#                              answered by looking the order up — but only
#                              AFTER tiers 1–4 had their chance. That is why
#                              "What is the status of my order ORD1042?"
#                              -> Delivery Issue, while "I want a refund for
#                              order ORD1042" stays a Refund Inquiry.
#
# In short: a message containing the word "order" is only treated as an
# order-status (Delivery Issue) request when it matched none of the more
# specific money or return wording above.
#
# Keywords are matched as WHOLE WORDS (see _contains_keyword), so "charge"
# does not match "charger" and "order" does not match "borderline".
RULES = [
    # (keywords to look for,          category,            next action)

    # Tier 1 — money back
    (["refund", "refunds", "refunded", "money back", "reimburse",
      "reimbursement"],
                                      "Refund Inquiry",    "Check refund status"),

    # Tier 2 — the payment transaction itself
    (["payment", "payments", "transaction", "charged", "charge", "billing",
      "declined", "invoice", "card"],
                                      "Payment Issue",     "Check payment status"),

    # Tier 3 — returns and exchanges (policy questions, not deliveries)
    (["return policy", "returns", "return", "exchange", "send it back",
      "send back"],
                                      "General Inquiry",   "Retrieve return policy"),

    # Tier 4 — specific delivery wording
    (["delivery", "deliver", "delivered", "arrived", "arrive", "arriving",
      "not received", "receive", "received", "late", "delay", "delayed",
      "shipped", "shipment", "tracking", "package", "parcel", "courier",
      "dispatch", "dispatched", "on its way"],
                                      "Delivery Issue",    "Look up the order"),

    # Tier 5 — order-status fallback for a bare "order" mention
    (["order", "orders"],
                                      "Delivery Issue",    "Look up order status"),
]

FALLBACK_CATEGORY = "Unknown Request"
FALLBACK_ACTION = "Ask for clarification"


def _contains_keyword(message: str, keyword: str) -> bool:
    """True when ``keyword`` appears in ``message`` as a whole word/phrase.

    Word boundaries prevent accidental partial matches: "charge" does not
    match "charger", and "order" does not match "borderline". Both arguments
    are expected to be lowercase already.
    """
    return re.search(r"\b" + re.escape(keyword) + r"\b", message) is not None


def classify_node(normalized_message: str) -> tuple[str, str]:
    """Return (category, next_action) for an already-lowercased message.

    Rules are checked in list order, so the first rule with any matching
    keyword decides the category.
    """
    for keywords, category, action in RULES:
        for keyword in keywords:
            if _contains_keyword(normalized_message, keyword):
                return category, action
    # No rule matched: fall through to the default branch.
    return FALLBACK_CATEGORY, FALLBACK_ACTION


# ---------------------------------------------------------------------------
# NODE 4 — Extract & Validate Order ID  (NEW in Stage 2)
# ---------------------------------------------------------------------------
# Purpose: pull an order ID out of the customer's message.
# An order ID looks like "ORD" followed by exactly four digits:
# ORD1042, ord2050, Ord7777 ...
#
# re.IGNORECASE makes the pattern match upper-, lower- or mixed-case text.
# The \b word boundaries are important: without them "ORD12345" would match
# the first four digits and be treated as a real order ID.
ORDER_ID_PATTERN = re.compile(r"\bORD\d{4}\b", re.IGNORECASE)

# Order-ID-LIKE text that is NOT in the accepted format. Two shapes are
# recognized so the customer can be told what to fix instead of just being
# asked again: "ORD12" / "ORD12345" / "ORD 1042", and "order 1042" /
# "order number 1042". These patterns are only consulted when no valid ID was
# found, so they can never override a correct ORD1042.
ORDER_LIKE_PATTERN = re.compile(r"\bORD[-\s]?\d+\b", re.IGNORECASE)
ORDER_NUMBER_PATTERN = re.compile(
    r"\border\s*(?:id|number|no\.?|#)?\s*(?:is|:)?\s*[:#]?\s*(\d{3,})\b",
    re.IGNORECASE,
)


def extract_order_id(message: str) -> str | None:
    """Return the order ID found in the message as UPPERCASE (e.g.
    "ORD1042"), or None when the message contains no order ID."""
    match = ORDER_ID_PATTERN.search(message)   # find the first ORD + 4 digits
    if match:
        return match.group(0).upper()          # normalize ord2050 -> ORD2050
    return None


def validate_order_id(order_id: str | None) -> bool:
    """Check whether an order ID was actually found.
    Returns True if we have an ID, False if it is None."""
    return order_id is not None


def find_malformed_order_id(message: str) -> str | None:
    """Return order-ID-like text in the wrong format, or None.

    Called only after extract_order_id() found nothing, so a correctly
    formatted order ID always wins. The returned text is used only to choose
    a clearer reply; it is never treated as a real order record.
    """
    # Skip any token that is a valid order ID, so this function can be called
    # safely even when the message also contains a correct ID.
    for match in ORDER_LIKE_PATTERN.finditer(message):
        candidate = match.group(0)
        if ORDER_ID_PATTERN.fullmatch(candidate) is None:
            return candidate

    match = ORDER_NUMBER_PATTERN.search(message)
    if match:
        return match.group(0)
    return None


# ---------------------------------------------------------------------------
# NODE 5 — Output
# ---------------------------------------------------------------------------
# Bundle the original message, category, action, extracted ID, and any
# matching sample order into a structured dictionary.
def output_node(original_message: str, category: str, next_action: str,
                order_id: str | None = None,
                order_details: dict | str | None = None,
                refund_details: dict | str | None = None,
                payment_details: dict | str | None = None,
                customer_response: str | None = None,
                input_error: str | None = None) -> dict:
    return {
        "schema_version": JSON_SCHEMA_VERSION,
        "original_message": original_message,
        "category": category,
        "next_action": next_action,
        "order_id": order_id,
        "order_details": order_details,
        "refund_details": refund_details,
        "payment_details": payment_details,
        # Filled in by run_workflow() after the lookup is complete.
        "customer_response": customer_response,
        # None normally. Set to "invalid_message", "malformed_order_id",
        # "database_unavailable", or "unexpected_error" when the request
        # could not be handled normally. The response generator uses it to
        # pick the right explanation.
        "input_error": input_error,
        # Stage 6 — every freshly generated response starts as Pending and is
        # NOT allowed to proceed until a human approves it.
        "approval_status": approval.PENDING,
        "approval": None,
        "can_proceed": False,
    }


# ---------------------------------------------------------------------------
# CONNECTIONS — wiring the nodes together
# ---------------------------------------------------------------------------
def run_workflow(message: str) -> dict:
    """Run the full pipeline and ALWAYS return a result dictionary.

    Two safety nets live here:
        - DatabaseError is reported as a database problem (never as an
          unknown order) with customer-safe wording.
        - Any other unexpected exception is caught so the terminal program
          finishes cleanly instead of printing a traceback.

    A normal "order not found" is NOT an error: it is handled inside
    _run_workflow() as a valid result, because the database answered fine.
    """
    try:
        return _run_workflow(message)
    except DatabaseError:
        return _error_result(message, "database_unavailable",
                             ACTION_DATABASE_UNAVAILABLE)
    except Exception:
        # Deliberately broad: this is the last line of defence for a demo
        # program. The technical detail is not shown to the customer.
        return _error_result(message, "unexpected_error",
                             ACTION_UNEXPECTED_ERROR)


def _run_workflow(message: str) -> dict:
    """The normal pipeline, including the Stage 3/4 local order lookup.

    Each line below is a "connection": the result of one node is handed to
    the next node.
    """
    # Stage 7 — reject an empty or unreadable message before doing any work.
    if validate_message(message) is not None:
        return _error_result(message, "invalid_message",
                             ACTION_ASK_FOR_MESSAGE)

    raw = input_node(message)                     # connection 1 -> 2
    normalized = normalize_node(raw)              # connection 2 -> 3
    category, action = classify_node(normalized)  # connection 3 -> 4

    # Stage 2 — extract the order ID from the ORIGINAL message (the
    # lowercased copy would give us "ord1042", so we uppercase it back),
    # then validate whether anything was found.
    order_id = extract_order_id(raw)              # connection 4a
    order_id_found = validate_order_id(order_id)  # connection 4b
    # Only look for a badly formatted ID when no valid one was found.
    malformed_order_id = None
    if not order_id_found:
        malformed_order_id = find_malformed_order_id(raw)

    order_details = None
    refund_details = None
    payment_details = None
    input_error = None

    # Delivery, refund, and payment requests use the order database. General
    # inquiries and unknown requests keep the existing classifier's action and
    # never touch the database.
    if category in ("Delivery Issue", "Refund Inquiry", "Payment Issue"):

        if not order_id_found:
            # No lookup is attempted without an order ID.
            if malformed_order_id:
                input_error = "malformed_order_id"
                action = ACTION_ASK_FOR_VALID_ORDER_ID
            else:
                action = ACTION_ASK_FOR_ORDER_ID
        else:
            try:
                # Safe to call each run: it only creates what is missing and
                # migrates older databases in place. Records are preserved.
                create_database()

                if category == "Delivery Issue":
                    order_details = get_order(order_id)
                    details = order_details
                    found_action = "Order found in sample database"
                elif category == "Refund Inquiry":
                    refund_details = get_refund_status(order_id)
                    details = refund_details
                    found_action = "Refund status found in sample database"
                else:  # Payment Issue
                    payment_details = get_payment_status(order_id)
                    details = payment_details
                    found_action = "Payment status found in sample database"

                if details is None:
                    # The database answered correctly: this ID is simply not
                    # one of the sample records.
                    action = "Order not found in sample database"
                    not_found_text = "Order not found in sample database"
                    if category == "Delivery Issue":
                        order_details = not_found_text
                    elif category == "Refund Inquiry":
                        refund_details = not_found_text
                    else:
                        payment_details = not_found_text
                else:
                    action = found_action

            except DatabaseError:
                # A database problem is NOT an unknown order. Say clearly that
                # the lookup failed, and keep SQL/paths/details out of view.
                input_error = "database_unavailable"
                action = ACTION_DATABASE_UNAVAILABLE
                if category == "Delivery Issue":
                    order_details = DATABASE_UNAVAILABLE_TEXT
                elif category == "Refund Inquiry":
                    refund_details = DATABASE_UNAVAILABLE_TEXT
                else:
                    payment_details = DATABASE_UNAVAILABLE_TEXT

    # The final result includes Stage 2's ID plus any Stage 3/4 lookup details.
    result = output_node(raw, category, action, order_id, order_details,
                         refund_details, payment_details,
                         input_error=input_error)

    # Stage 5 — turn the completed result into a customer-friendly message.
    # generate_response() only reads this dictionary, so there is no circular
    # import between flowpilot.py and response_generator.py.
    result["customer_response"] = generate_response(result)
    return result


def _error_result(message: str | None, input_error: str, action: str) -> dict:
    """Build a safe result for a request that could not be processed."""
    original_message = message if isinstance(message, str) else ""
    result = output_node(original_message, FALLBACK_CATEGORY, action,
                         None, None, None, None, input_error=input_error)
    result["customer_response"] = generate_response(result)
    return result


# ---------------------------------------------------------------------------
# STAGE 6 — HUMAN-IN-THE-LOOP REVIEW
# ---------------------------------------------------------------------------
# run_workflow() stays exactly as before, so every earlier stage and every
# existing test still works without a keyboard. run_workflow_with_approval()
# adds the review step on top of it:
#
#   Customer Message -> Classification -> Order ID Extraction
#   -> Database Lookup -> Response Generation -> Human Review
#   -> Approval or Rejection -> Final Result
#
def run_workflow_with_approval(message: str, input_func=input,
                               output_func=print) -> dict:
    """Generate a response, then require a human decision before finishing.

    The generated response is displayed first. The result returned from
    run_workflow() is then extended with:
        approval       -> the saved audit-log record
        approval_status -> Pending / Approved / Rejected
        can_proceed     -> True only when a human approved the response

    If the reviewer rejects the response, can_proceed stays False and the
    response must not move to the next workflow stage.
    """
    result = run_workflow(message)

    decision = approval.review_response(
        result["customer_response"],
        input_func=input_func,
        output_func=output_func,
    )

    if decision == approval.PENDING:
        # No human decision was received. Record nothing: Pending is the
        # starting state, not a decision, and it must never unlock the next
        # stage on its own.
        output_func("")
        output_func("No approval decision was recorded.")
        return result

    approval_record = approval.record_approval(
        response=result["customer_response"],
        decision=decision,
        customer_message=result["original_message"],
    )

    result["approval"] = approval_record
    result["approval_status"] = approval_record["approval_status"]
    result["can_proceed"] = approval.can_proceed(approval_record["approval_status"])
    return result


def require_approval_to_proceed(result: dict) -> bool:
    """Guard for the next workflow stage.

    Returns True only for an approved result. Anything else (Pending,
    Rejected, or a result that was never reviewed) raises PermissionError,
    so a response cannot move forward by accident.
    """
    if not result.get("can_proceed"):
        status = result.get("approval_status", approval.PENDING)
        raise PermissionError(
            f"This response is {status} and cannot move to the next stage. "
            "Only an Approved response may proceed."
        )
    return True


# ---------------------------------------------------------------------------
# LLM path (Stage 10.1)
# ---------------------------------------------------------------------------
# This path reuses the existing workflow result and database lookup. The LLM
# is used only for tone and wording. It never approves, rejects, executes,
# or authorizes anything, and the existing human-in-the-loop step is still
# mandatory when approval is enabled.


def run_workflow_with_llm(message: str) -> dict:
    """Run the workflow and attempt an LLM-based response where possible.

    If the LLM module is unavailable, the API key is missing, the provider
    fails, or the generated text fails validation, the existing template
    generator is used as a fallback.

    This path does not approve, reject, execute, or authorize anything. Any
    human-in-the-loop approval is handled separately by
    run_workflow_with_approval().
    """
    provider = None
    if _LLM_MODULE_AVAILABLE and get_openai_provider is not None:
        try:
            provider = get_openai_provider()
        except Exception:
            provider = None

    return run_workflow_with_provider(message, provider)


def run_workflow_with_provider(message: str, provider: LLMProvider | None) -> dict:
    """Run one lookup, generate with the selected provider, and use real templates on failure."""
    result = run_workflow(message)
    if result.get("input_error"):
        return result

    candidate = None
    if provider is not None and _LLM_MODULE_AVAILABLE:
        try:
            candidate = _generate_llm_text(result, provider)
        except Exception:
            candidate = None

    result["customer_response"] = candidate or generate_response(result)
    return result


def run_workflow_with_provider_and_approval(
    message: str,
    provider: LLMProvider | None,
    input_func=input,
    output_func=print,
) -> dict:
    """Use a selected provider but keep human review as the only approval path."""
    result = run_workflow_with_provider(message, provider)

    decision = approval.review_response(
        result["customer_response"],
        input_func=input_func,
        output_func=output_func,
    )
    if decision == approval.PENDING:
        output_func("")
        output_func("No approval decision was recorded.")
        return result

    approval_record = approval.record_approval(
        response=result["customer_response"],
        decision=decision,
        customer_message=result["original_message"],
    )
    result["approval"] = approval_record
    result["approval_status"] = approval_record["approval_status"]
    result["can_proceed"] = approval.can_proceed(approval_record["approval_status"])
    return result

def run_workflow_with_llm_and_approval(
    message: str,
    input_func=input,
    output_func=print,
) -> dict:
    """LLM path plus the existing mandatory human approval step.

    The LLM is used only for tone and wording. A human must still approve
    or reject the response before it can proceed.
    """
    provider = None
    if _LLM_MODULE_AVAILABLE and get_openai_provider is not None:
        try:
            provider = get_openai_provider()
        except Exception:
            provider = None
    return run_workflow_with_provider_and_approval(
        message, provider, input_func=input_func, output_func=output_func
    )


def _generate_llm_text(result: dict, provider: LLMProvider) -> str | None:
    """Attempt an LLM response and fall back to None on any problem.

    The caller is responsible for using the template fallback when this
    returns None.
    """
    from llm_response_generator import generate_llm_response

    candidate = generate_llm_response(result, provider)
    if candidate:
        return candidate
    return None


# ---------------------------------------------------------------------------
# Runner: how to enter a test message and run the workflow
# ---------------------------------------------------------------------------
# Stage 5 required test cases, in the order requested by the specification.
TEST_MESSAGES = [
    "My order ORD1042 has not arrived.",
    "I want a refund for order ORD2050.",
    "Has my refund for ORD4261 been completed?",
    "My payment for ORD5734 is pending.",
    "Why did the payment for ORD6842 fail?",
    "What is your return policy?",
    "I need help changing my account details.",
    "I want a refund but I do not have my order number.",
    "My order ORD9999 has not arrived.",
]


# ---------------------------------------------------------------------------
# Stage 6 classification checks
# ---------------------------------------------------------------------------
# Each pair is (message, expected category). The messages deliberately mix
# categories, capitalization, and overlapping keywords such as "refund" +
# "order" or "payment" + "order".
CLASSIFICATION_TESTS = [
    # 1–6: the mutually overlapping "money" cases.
    ("I want a refund for order ORD1042.",              "Refund Inquiry"),
    ("I WANT A REFUND FOR ORDER ORD1042.",              "Refund Inquiry"),
    ("My payment for ORD5734 is pending.",              "Payment Issue"),
    ("My Payment For ORD5734 Is Pending.",              "Payment Issue"),
    ("My card was declined for order ORD2050.",         "Payment Issue"),
    ("MY CARD WAS DECLINED FOR ORDER ORD2050.",         "Payment Issue"),

    # 7–10: refund wording outranks delivery AND payment wording.
    ("My order ORD1042 has not arrived, and I want a refund.",
                                                        "Refund Inquiry"),
    ("Why did the payment for ORD6842 fail? I also want a refund.",
                                                        "Refund Inquiry"),
    ("I have not received my package and my refund is late.",
                                                        "Refund Inquiry"),
    ("Can I get my money back for ORD4261?",             "Refund Inquiry"),

    # 11–13: refund status questions.
    ("Has my refund for ORD4261 been completed?",       "Refund Inquiry"),
    ("IS MY REFUND STILL PROCESSING?",                  "Refund Inquiry"),
    ("I have not requested a refund yet.",              "Refund Inquiry"),

    # 14–16: returns are policy questions, not delivery problems.
    ("I want to return my order ORD1042.",              "General Inquiry"),
    ("What is your return policy?",                     "General Inquiry"),
    ("I would like to exchange an item from order ORD4261.",
                                                        "General Inquiry"),

    # 17–19: genuine delivery complaints, including mixed case.
    ("My order ORD1042 has not arrived.",               "Delivery Issue"),
    ("MY ORDER ORD3187 WAS DELAYED AND MY CARD WAS DECLINED.",
                                                        "Payment Issue"),
    ("Where is my order ORD3187?",                      "Delivery Issue"),

    # 20–22: bare "order" mention -> order-status fallback (Delivery Issue).
    ("What is the status of my order ORD1042?",         "Delivery Issue"),
    ("what is the status of MY order ord1042?",         "Delivery Issue"),
    ("Please tell me when my parcel will arrive.",      "Delivery Issue"),

    # 23–25: no rule matches -> fallback category.
    ("Can you help me change my account details?",      "Unknown Request"),
    ("Hello, I need some help.",                        "Unknown Request"),
    ("Thanks, that is all for today.",                  "Unknown Request"),
]


def run_classification_tests() -> bool:
    """Print every classification test and return True if all of them pass."""
    print("\nFlowPilot AI - classification tests (Stage 6)\n")
    all_passed = True
    for i, (message, expected) in enumerate(CLASSIFICATION_TESTS, start=1):
        category, _action = classify_node(normalize_node(message))
        passed = category == expected
        all_passed = all_passed and passed
        print(f"{i:>2}. [{'PASS' if passed else 'FAIL'}] {message}")
        print(f"    expected: {expected}")
        print(f"    actual  : {category}")
    print(f"\n{classification_pass_count()}/{len(CLASSIFICATION_TESTS)}"
          " classification tests passed.\n")
    return all_passed


def classification_pass_count() -> int:
    """Count how many built-in classification test messages pass."""
    passed = 0
    for message, expected in CLASSIFICATION_TESTS:
        if classify_node(normalize_node(message))[0] == expected:
            passed += 1
    return passed


def print_result(result: dict, show_response: bool = True) -> None:
    """Print one result dictionary for a human reader.

    ``show_response=False`` skips the generated text, which the review step
    has already displayed.
    """
    print(f"  Message : {result['original_message']}")
    print(f"  Category: {result['category']}")
    print(f"  Order ID: {result['order_id']}")
    print(f"  Action  : {result['next_action']}")
    print(f"  Order details: {result['order_details']}")

    refund_details = result.get("refund_details")
    if refund_details is not None:
        if isinstance(refund_details, dict):
            print(f"  Refund status: {refund_details['refund_status']}")
            print(f"  Refund amount: ${refund_details['refund_amount']:.2f}")
        else:
            print(f"  Refund details: {refund_details}")

    payment_details = result.get("payment_details")
    if payment_details is not None:
        if isinstance(payment_details, dict):
            print(f"  Payment method: {payment_details['payment_method']}")
            print(f"  Transaction status: {payment_details['transaction_status']}")
        else:
            print(f"  Payment details: {payment_details}")

    customer_response = result.get("customer_response")
    if customer_response and show_response:
        print("  Customer response (generated, not sent):")
        for line in customer_response.splitlines():
            print(f"    {line}")

    # Stage 6 — make the difference between a generated response and an
    # approved one obvious at the end of the run.
    approval_record = result.get("approval")
    approval_status = result.get("approval_status", approval.PENDING)
    if approval_record:
        print(f"  Approval status: {approval_status}"
              f" (id {approval_record['approval_id']},"
              f" reviewed at {approval_record['reviewed_at']})")
    else:
        print(f"  Approval status: {approval_status} (not reviewed)")

    if result.get("can_proceed"):
        print("  Next workflow stage: ALLOWED - the response is approved.")
    else:
        print("  Next workflow stage: BLOCKED - the response must not proceed.")


# ---------------------------------------------------------------------------
# JSON export  (NEW in Stage 7)
# ---------------------------------------------------------------------------
# The file is written beside this Python file, so the location is predictable
# no matter which folder the terminal is in.
JSON_OUTPUT_PATH = Path(__file__).with_name("workflow_result.json")

# The exact fields exported, in this order.
JSON_SCHEMA_VERSION = "1.0"
JSON_FIELDS = (
    "schema_version",
    "original_message",
    "category",
    "order_id",
    "next_action",
    "order_details",
    "refund_details",
    "payment_details",
    "customer_response",
    "approval_status",
    "approval",
    "can_proceed",
)


def export_result_to_json(result: dict,
                          file_path: str | Path = JSON_OUTPUT_PATH) -> str:
    """Write one workflow result to workflow_result.json and return its path.

    JSON serialization in one sentence: ``json.dump()`` walks the Python
    values and writes them as JSON text, so dictionaries become JSON objects
    ``{...}``, strings become quoted JSON strings, numbers stay numbers, and
    ``None`` becomes ``null``.

    Options used:
        indent=2         -> readable, one field per line
        ensure_ascii=False -> keep readable characters as-is instead of
                            writing escaped \\uXXXX sequences
        default=str      -> safety net so an unexpected value type can never
                            crash the export

    Only the fields in JSON_FIELDS are written, always in the same order.
    """
    payload = {
        field: JSON_SCHEMA_VERSION if field == "schema_version" else result.get(field)
        for field in JSON_FIELDS
    }
    try:
        with open(file_path, "w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=2, ensure_ascii=False,
                      default=str)
    except OSError as error:
        # File-system problems (permissions, missing folder) are reported
        # without exposing the full system path in the message.
        raise RuntimeError("Could not save workflow_result.json.") from error
    return str(file_path)


# ---------------------------------------------------------------------------
# Terminal commands
# ---------------------------------------------------------------------------
def main(argv: list[str]) -> int:
    """Run the backward-compatible CLI with an explicit optional provider mode."""
    parser = argparse.ArgumentParser(description="FlowPilot AI fictional support demo")
    parser.add_argument("message_parts", nargs="*", help="Customer message (legacy form)")
    parser.add_argument("--message", help="Customer message")
    parser.add_argument(
        "--mode", choices=("template", "mock", "openai"),
        help="Response provider; defaults to template mode",
    )
    parser.add_argument("--llm", action="store_true", help="Legacy alias for --mode openai")
    parser.add_argument("--json", action="store_true", dest="export_json")
    parser.add_argument("--db", help="SQLite database path")
    parser.add_argument("--classify-tests", action="store_true")
    options = parser.parse_args(argv)

    if options.classify_tests:
        return 0 if run_classification_tests() else 1

    if options.message and options.message_parts:
        parser.error("Use either --message or positional message text, not both.")
    if options.llm and options.mode not in (None, "openai"):
        parser.error("--llm cannot be combined with a non-OpenAI --mode.")

    message = options.message or " ".join(options.message_parts)
    if not message:
        if options.mode or options.llm or options.export_json:
            parser.error("A customer message is required for the selected options.")
        print("\nFlowPilot AI - test run (local sample database demo)\n")
        for i, test_message in enumerate(TEST_MESSAGES, start=1):
            print(f"Test {i}")
            print_result(run_workflow(test_message))
            print()
        return 0

    mode = options.mode or ("openai" if options.llm else "template")
    if options.db:
        database_path = Path(options.db).expanduser()
        database.DATABASE_PATH = database_path

    provider = None
    if mode == "mock":
        if not _LLM_MODULE_AVAILABLE:
            print("Mock mode is unavailable because the response provider module could not be loaded.")
            return 2
        from llm_response_generator import MockLLM

        mock_reply = "I can help with your request."
        provider = MockLLM({
            "Delivery Issue": mock_reply,
            "Refund Inquiry": mock_reply,
            "Payment Issue": mock_reply,
            "General Inquiry": mock_reply,
            "Unknown Request": mock_reply,
        })
    elif mode == "openai":
        provider = get_openai_provider() if get_openai_provider is not None else None
        if provider is None:
            print("OpenAI mode requires OPENAI_API_KEY. No response was generated.")
            return 2

    print("\nFlowPilot AI - workflow with human review")
    try:
        if mode == "template":
            result = run_workflow_with_approval(message)
        else:
            result = run_workflow_with_provider_and_approval(message, provider)
    except ValueError as error:
        print(f"  The approval could not be recorded: {error}")
        return 1
    except RuntimeError:
        print("  The approval could not be recorded because the sample "
              "order database is unavailable.")
        return 1

    print("\nFlowPilot AI - final result")
    print(f"  Response mode: {mode}")
    print_result(result, show_response=False)

    if options.export_json:
        try:
            path = export_result_to_json(result)
        except RuntimeError as error:
            print(f"  JSON export failed: {error}")
            return 1
        print(f"  JSON saved to: {path}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
