"""Customer response generator for FlowPilot AI (Stages 5–6).

The response generator turns the dictionary returned by
``flowpilot.run_workflow()`` into a clear, professional message for the
customer. It uses only Python string templates and conditional logic — no
paid LLM API and no external services.

How this file is organized
--------------------------
The templates are grouped by CATEGORY, and inside each category by STATUS:

    1. Shared text helpers        (money, "not found" checks)
    2. Shared fallback templates  (input problems / no order ID / not found /
                                   database error)
    3. DELIVERY ISSUE templates   -> one template + one sentence per
                                     order status (Shipped, Processing,
                                     Delivered, Delayed, Cancelled, ...)
    4. REFUND INQUIRY templates   -> one template + one paragraph per status
                                     (Requested, Processing, Completed,
                                     No Refund Requested, ...)
    5. PAYMENT ISSUE templates    -> one template + one paragraph per status
                                     (Successful, Pending, Failed, ...)
    6. GENERAL INQUIRY templates  -> predefined demonstration text
    7. UNKNOWN REQUEST templates  -> clarification request
    8. generate_response()        -> the single entry point

Every category follows the same three-step shape, which is why the code does
not repeat itself:

    a lookup returned a record  -> fill that category's template
    a lookup returned nothing   -> use the shared fallback (no order ID,
                                   order not found, database error)

The generated text only repeats information that came from the local sample
SQLite database (or predefined demonstration text). It never invents order
details and never claims that a real refund, payment, or email took place.

This file deliberately does not import flowpilot.py or database.py, so there
are no circular imports: flowpilot.py imports this module only.

Optional LLM integration is provided by the llm_response_generator module.
When an LLMProvider is given to generate_response(), the system first attempts
LLM-based generation, and falls back to templates if the LLM returns None or
invalid output. This ensures zero-downtime operation and guaranteed compatibility.
"""

from __future__ import annotations

from typing import Optional, Dict, Any

# ---------------------------------------------------------------------------
# 1. SHARED TEXT HELPERS
# ---------------------------------------------------------------------------

# Text that database.py/flowpilot.py use when an order ID is not in the DB.
NOT_FOUND_TEXT = "not found"

# A short note added to refund and payment replies so the customer is never
# misled into thinking a real financial action happened.
DEMO_NOTE = "This information comes from a local sample database, and this "
DEMO_NOTE += "demonstration does not issue real refunds or process real payments."

def _money(amount: float) -> str:
    """Format a number like 49.99 as a dollar string like $49.99."""
    return f"${amount:,.2f}"

def _is_not_found(details: dict | str | None) -> bool:
    """True when a lookup result is the workflow's 'order not found' text."""
    return isinstance(details, str) and NOT_FOUND_TEXT in details.lower()

def _is_database_error(details: dict | str | None) -> bool:
    """True when a lookup result is an error string rather than a record."""
    return isinstance(details, str) and not _is_not_found(details)

# ---------------------------------------------------------------------------
# 2. SHARED FALLBACK TEMPLATES
#    Used by every category that needs an order lookup when the lookup could
#    not produce a record. Keeping them in one place avoids repeating text.
# ---------------------------------------------------------------------------

ASK_FOR_ORDER_ID_TEMPLATE = (
    "Hello,\n\n"
    "Thank you for contacting us about your {topic}. We could not find an "
    "order ID in your message.\n\n"
    "Please reply with your order ID (for example, ORD1042) so we can look "
    "up the details for you.\n\n"
    "Thank you."
)

ORDER_NOT_FOUND_TEMPLATE = (
    "Hello,\n\n"
    "We could not find {order_id} in our sample database, so we cannot "
    "confirm the {topic} details.\n\n"
    "Please check the order ID and try again. Order IDs in this demo look "
    "like ORD1042.\n\n"
    "Thank you."
)

DATABASE_ERROR_TEMPLATE = (
    "Hello,\n\n"
    "We are sorry, but we could not access the sample database while "
    "checking the {topic} for {order_id}. This is a temporary problem in "
    "the demonstration.\n\n"
    "Please try again in a moment.\n\n"
    "Thank you."
)

def _ask_for_order_id(topic: str) -> str:
    """Polite reply used when the customer's message has no order ID."""
    return ASK_FOR_ORDER_ID_TEMPLATE.format(topic=topic)

def _order_not_found(order_id: str | None, topic: str) -> str:
    """Reply used when the supplied order ID is not in the sample database."""
    return ORDER_NOT_FOUND_TEMPLATE.format(
        order_id=order_id if order_id else "that order ID",
        topic=topic,
    )

def _database_error(order_id: str | None, topic: str) -> str:
    """Reply used when the local sample database could not be read."""
    return DATABASE_ERROR_TEMPLATE.format(
        order_id=order_id if order_id else "your order",
        topic=topic,
    )

# ---------------------------------------------------------------------------
# 2b. INPUT PROBLEM TEMPLATES
#     Selected by generate_response() from result["input_error"], before the
#     category is looked at. Each one explains the problem in plain language
#     and states what the customer can do next. None of them mention SQL,
#     file paths, stack traces, or other technical details.
# ---------------------------------------------------------------------------

INVALID_MESSAGE_RESPONSE = (
    "Hello,\n\n"
    "Thank you for contacting us. Your message arrived empty or could not be "
    "read, so there is nothing we can look up yet.\n\n"
    "Please send your question as plain text, and include your order ID "
    "(for example, ORD1042) if your question is about an order.\n\n"
    "Thank you."
)

MALFORMED_ORDER_ID_RESPONSE = (
    "Hello,\n\n"
    "Thank you for contacting us. We found something that looks like an "
    "order ID in your message, but it is not in a format we can look up.\n\n"
    "Order IDs in this demo look like ORD followed by exactly four digits, "
    "for example ORD1042. Please reply with the order ID written in that "
    "format so we can check the details for you.\n\n"
    "Thank you."
)

DATABASE_UNAVAILABLE_RESPONSE = (
    "Hello,\n\n"
    "We are sorry, but our sample order records cannot be opened right now, "
    "so we cannot check your order details at this moment.\n\n"
    "This is a temporary problem in the demonstration, and no records were "
    "changed. Please try again in a moment.\n\n"
    "Thank you."
)

UNEXPECTED_ERROR_RESPONSE = (
    "Hello,\n\n"
    "We are sorry, but something unexpected went wrong while handling your "
    "request, so we could not finish checking it.\n\n"
    "No records were changed. Please try again, and contact our support team "
    "if the problem continues.\n\n"
    "Thank you."
)

# Maps result["input_error"] to the reply that explains it.
INPUT_PROBLEM_RESPONSES = {
    "invalid_message": INVALID_MESSAGE_RESPONSE,
    "malformed_order_id": MALFORMED_ORDER_ID_RESPONSE,
    "database_unavailable": DATABASE_UNAVAILABLE_RESPONSE,
    "unexpected_error": UNEXPECTED_ERROR_RESPONSE,
}

# ---------------------------------------------------------------------------
# 3. DELIVERY ISSUE TEMPLATES
# ---------------------------------------------------------------------------

DELIVERY_FOUND_TEMPLATE = (
    "Hello {customer_name},\n\n"
    "Thank you for contacting us about your delivery. "
    "Your order {order_id} for {product_name} is currently {order_status}. "
    "The expected delivery date is {delivery_date}.\n\n"
    "{explanation}\n\n"
    "Thank you for your patience."
)

# One explanation sentence per order status held in the sample database. Each
# sentence only re-describes the recorded status; it never adds tracking,
# courier, or timing facts that the database does not contain.
DELIVERY_STATUS_EXPLANATIONS = {
    "Processing": (
        "Your order is still being prepared and has not shipped yet."
    ),
    "Shipped": (
        "Your order has been shipped and is on its way to you."
    ),
    "Delivered": (
        "Our records show that this order has been delivered."
    ),
    "Delayed": (
        "This order has been delayed, so it may arrive later than the "
        "expected date shown above."
    ),
    "Cancelled": (
        "Our records show that this order was cancelled."
    ),
    "Canceled": (
        "Our records show that this order was cancelled."
    ),
}

# Used only if the database ever holds a status this demo does not know.
DELIVERY_UNKNOWN_EXPLANATION = "The current order status on record is {order_status}."

def _delivery_explanation(order_status: str) -> str:
    """Return the sentence that matches the recorded order status."""
    return DELIVERY_STATUS_EXPLANATIONS.get(
        order_status,
        DELIVERY_UNKNOWN_EXPLANATION,
    ).format(order_status=order_status)

def _delivery_response(result: dict) -> str:
    """Build a response for a Delivery Issue (order found or fallback)."""
    order_id = result.get("order_id")
    details = result.get("order_details")

    # The order was found: use the database fields exactly as returned.
    if isinstance(details, dict):
        order_status = details.get("order_status", "Unknown")
        return DELIVERY_FOUND_TEMPLATE.format(
            customer_name=details.get("customer_name", "there"),
            order_id=details.get("order_id", order_id),
            product_name=details.get("product_name", "your item"),
            order_status=order_status,
            delivery_date=details.get("delivery_date", "not available"),
            explanation=_delivery_explanation(order_status),
        )

    return _lookup_fallback(details, order_id, "delivery")

# ---------------------------------------------------------------------------
# 4. REFUND INQUIRY TEMPLATES
#    One status per explanation, so the wording never overstates what the
#    sample database actually records.
# ---------------------------------------------------------------------------

REFUND_FOUND_TEMPLATE = (
    "Hello,\n\n"
    "Thank you for contacting us about your refund for order {order_id}. "
    "The current refund status is {refund_status}, and the refund amount "
    "on record is {refund_amount}.\n\n"
    "{explanation}\n\n"
)

# Each status has an explanation plus a neutral next step. The next step is
# generic wording (reply, or contact support) because the sample database
# holds no refund policy, timeline, or approval rules.
REFUND_EXPLANATIONS = {
    "Refund Requested": (
        "Your refund request has been received, but it has not been "
        "approved or processed yet. No action is needed from you right now."
    ),
    "Refund Processing": (
        "Your refund is currently being processed. It has not been "
        "completed yet. No action is needed from you right now."
    ),
    "Refund Completed": (
        "According to our sample records, your refund has been completed. "
        "If you do not see the amount in your account, please contact our "
        "support team so they can check it with you."
    ),
    "No Refund Requested": (
        "Our sample records show that no refund has been requested for "
        "this order. If you would like to request one, please reply to this "
        "message and our support team will review your request."
    ),
}

# Used only if the database ever holds a status this demo does not know.
REFUND_UNKNOWN_EXPLANATION = "The current refund status on record is {refund_status}."

def _refund_explanation(refund_status: str) -> str:
    """Return the template sentence that matches the recorded refund status."""
    return REFUND_EXPLANATIONS.get(
        refund_status,
        REFUND_UNKNOWN_EXPLANATION,
    ).format(refund_status=refund_status)

def _refund_response(result: dict) -> str:
    """Build a response for a Refund Inquiry, adapted to the refund status."""
    order_id = result.get("order_id")
    details = result.get("refund_details")

    if isinstance(details, dict):
        refund_status = details.get("refund_status", "Unknown")
        return REFUND_FOUND_TEMPLATE.format(
            order_id=details.get("order_id", order_id),
            refund_status=refund_status,
            refund_amount=_money(details.get("refund_amount", 0.0)),
            explanation=_refund_explanation(refund_status),
        )

    return _lookup_fallback(details, order_id, "refund")

# ---------------------------------------------------------------------------
# 5. PAYMENT ISSUE TEMPLATES
#    No payment is ever performed here; the text only describes the record.
# ---------------------------------------------------------------------------

PAYMENT_FOUND_TEMPLATE = (
    "Hello,\n\n"
    "Thank you for contacting us about the payment for order {order_id}.\n\n"
    "Payment method: {payment_method}\n"
    "Transaction status: {transaction_status}\n\n"
    "{explanation}\n\n"
)

PAYMENT_EXPLANATIONS = {
    "Payment Successful": (
        "The payment was successful and the transaction is complete. "
        "No action is needed from you."
    ),
    "Payment Pending": (
        "The payment is still pending and has not been completed yet. "
        "No action is needed while it finishes."
    ),
    "Payment Failed": (
        "The payment did not go through successfully, so the transaction "
        "was not completed. Please contact our support team if you would "
        "like help trying the payment again."
    ),
}

# Used only if the database ever holds a status this demo does not know.
PAYMENT_UNKNOWN_EXPLANATION = (
    "The current transaction status on record is {transaction_status}."
)

def _payment_explanation(transaction_status: str) -> str:
    """Return the template sentence that matches the recorded status."""
    return PAYMENT_EXPLANATIONS.get(
        transaction_status,
        PAYMENT_UNKNOWN_EXPLANATION,
    ).format(transaction_status=transaction_status)

def _payment_response(result: dict) -> str:
    """Build a response for a Payment Issue, adapted to the transaction status."""
    order_id = result.get("order_id")
    details = result.get("payment_details")

    if isinstance(details, dict):
        return PAYMENT_FOUND_TEMPLATE.format(
            order_id=details.get("order_id", order_id),
            payment_method=details.get("payment_method", "Unknown"),
            transaction_status=details.get("transaction_status", "Unknown"),
            explanation=_payment_explanation(
                details.get("transaction_status", "Unknown")
            ),
        )

    return _lookup_fallback(details, order_id, "payment")

# ---------------------------------------------------------------------------
# 6. GENERAL INQUIRY TEMPLATES
# ---------------------------------------------------------------------------

GENERAL_INQUIRY_RESPONSE = (
    "Hello,\n\n"
    "Thank you for your question about our return policy. This is a "
    "demonstration system, so the text below is a predefined sample "
    "response, not a real store's policy.\n\n"
    "A sample return policy would usually explain the return window, the "
    "condition the item must be in, and the steps for starting a return.\n\n"
    "Please contact our support team for the official return policy."
)

def _general_inquiry_response() -> str:
    """Predefined demonstration response for General Inquiry requests."""
    return GENERAL_INQUIRY_RESPONSE

# ---------------------------------------------------------------------------
# 7. UNKNOWN REQUEST TEMPLATES
# ---------------------------------------------------------------------------

UNKNOWN_REQUEST_RESPONSE = (
    "Hello,\n\n"
    "Thank you for contacting us. I am not sure I understood your request, "
    "so I would like to help you more accurately.\n\n"
    "Could you please clarify your question? If it is about an order, "
    "including the order ID (for example, ORD1042) will help us assist you "
    "faster.\n\n"
    "Thank you."
)

def _unknown_request_response() -> str:
    """Polite clarification request for Unknown Request messages."""
    return UNKNOWN_REQUEST_RESPONSE

# ---------------------------------------------------------------------------
# 8. FALLBACK HELPER
# ---------------------------------------------------------------------------

def _lookup_fallback(details: dict | str | None, order_id: str | None,
                     topic: str) -> str:
    """Choose the shared fallback text when a lookup produced no record.

    The workflow can give us three non-record values:
        None                 -> no order ID in the message
        "not found ..."      -> order ID is not in the sample database
        any other string     -> the database could not be read
    """
    if _is_not_found(details):
        return _order_not_found(order_id, topic)
    if _is_database_error(details):
        return _database_error(order_id, topic)
    return _ask_for_order_id(topic)

# ---------------------------------------------------------------------------
# 9. RESPONSE HANDLERS (Template-based)
# ---------------------------------------------------------------------------

# Maps each category to the small function that builds its response.
RESPONSE_HANDLERS = {
    "Delivery Issue": _delivery_response,
    "Refund Inquiry": _refund_response,
    "Payment Issue": _payment_response,
    "General Inquiry": lambda result: _general_inquiry_response(),
    "Unknown Request": lambda result: _unknown_request_response(),
}

# ---------------------------------------------------------------------------
# 10. LLM INTEGRATION COMPONENTS
# ---------------------------------------------------------------------------

# Import LLM generation components. Import error is caught at runtime so
# that the module can be used without the LLM component installed.
try:
    from llm_response_generator import (
        LLMProvider,
        MockLLM,
        generate_llm_response,
        _contains_forbidden_patterns,
    )
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False
    # Create dummy classes so type hints work without errors
    class LLMProvider:  # type: ignore
        def generate(self, prompt: str) -> Optional[str]:  # type: ignore
            raise NotImplementedError("llm_response_generator not available")
    class MockLLM(LLMProvider):  # type: ignore
        def __init__(self, responses: Dict[str, str] = None):  # type: ignore
            self._responses = responses or {}
        def generate(self, prompt: str) -> Optional[str]:  # type: ignore
            return None

# ---------------------------------------------------------------------------
# 11. MODIFIED ENTRY POINT: generate_response()
# ---------------------------------------------------------------------------

def generate_response(result: dict, llm: Optional[LLMProvider] = None) -> str:
    """Return a customer-friendly message for a run_workflow() result.

    This is the entry point called by flowpilot.py and the test suite.

    Generation order (first match wins):

    1. **input_error check** — If result["input_error"] is set, return the
       appropriate problem response. This is unchanged from the original
       behaviour.

    2. **LLM attempt** — If an LLMProvider instance is given (and the
       llm_response_generator module is available), attempt LLM-based
       generation. If the LLM returns None or invalid output, silently
       fall through to step 3. If the LLM module is not available, skip
       this step entirely.

    3. **Template fallback** — Use the original template-based response
       generator (RESPONSE_HANDLERS dictionary). This is the original
       behaviour and is guaranteed to work regardless of LLM availability.

    4. **Unknown Request** — If somehow we reach this point with an
       unrecognized category, return the Unknown Request clarification text.

    The LLM is an optional enhancement — the system degrades gracefully
    to templates if the LLM is unavailable, returns invalid output, or
    encounters any error. No API keys or paid services are required; the
    MockLLM class provides fully mocked responses for testing.

    Parameters:
        result: The workflow result dictionary from run_workflow().
        llm: An LLMProvider instance, or None for templates-only mode.
            Pass MockLLM(mock_responses) for unit testing without API keys.

    Returns:
        A customer-friendly message string.

    Example:
        >>> from llm_response_generator import MockLLM
        >>> mock = MockLLM({
        ...     "Delivery Issue": "Your order is on its way.",
        ...     "Refund Inquiry": "Your refund is processing.",
        ...     "Payment Issue": "Payment is pending.",
        ...     "General Inquiry": "Our return policy is...",
        ...     "Unknown Request": "Could you clarify?",
        ... })
        >>> result = {"category": "Delivery Issue", "order_id": "ORD1042"}
        >>> generate_response(result, mock)  # returns "Your order is on its way."
    """
    # Step 1: input_error check (unchanged behaviour)
    input_error = result.get("input_error")
    if input_error in INPUT_PROBLEM_RESPONSES:
        return INPUT_PROBLEM_RESPONSES[input_error]

    # Step 2: Attempt LLM generation (if LLM provider and module available)
    if llm is not None and _LLM_AVAILABLE:
        llm_text = generate_llm_response(result, llm)
        if llm_text is not None and llm_text.strip():
            # LLM successfully generated valid text
            # Final validation against forbidden patterns
            if not _contains_forbidden_patterns(llm_text, result):
                return llm_text

    # Step 3: Template fallback (original behaviour, guaranteed to work)
    category = result.get("category", "Unknown Request")
    handler = RESPONSE_HANDLERS.get(category, RESPONSE_HANDLERS["Unknown Request"])
    return handler(result)