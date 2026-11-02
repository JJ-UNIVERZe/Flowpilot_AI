"""Human-in-the-loop approval for FlowPilot AI.

This file adds the review step that sits AFTER a customer response has been
generated. It never edits the response, never sends it, and never approves
anything on its own: a human reviewer must type a decision.

Approval states
---------------
    Pending   -> generated, not reviewed yet. Cannot proceed.
    Approved  -> a human accepted the response. May proceed.
    Rejected  -> a human refused the response. Must not proceed.

Two public functions
--------------------
    review_response(response)          ask a human to approve or reject
    record_approval(response, decision) save the decision, return the record

The decision is written to the local SQLite table ``approval_logs`` (see
database.py) so there is an audit trail of who-decided-what-when. This module
performs no external actions: no email, no refund, no order change, and no
payment.
"""

from datetime import datetime, timezone

import database

# ---------------------------------------------------------------------------
# 1. APPROVAL STATES
# ---------------------------------------------------------------------------
PENDING = "Pending"
APPROVED = "Approved"
REJECTED = "Rejected"

# Only these two states can be produced by a human review. Pending is the
# starting state and is never a valid review decision.
REVIEW_DECISIONS = (APPROVED, REJECTED)

PROMPT = "Approve or reject this response? Type 'a' to approve or 'r' to reject: "
INVALID_CHOICE_MESSAGE = ("That is not a valid choice. Please type 'a' to approve "
                          "or 'r' to reject.")

# Accepted spellings for each decision, so a reviewer can type a short answer
# or the full word.
APPROVE_ANSWERS = ("a", "approve", "approved", "y", "yes")
REJECT_ANSWERS = ("r", "reject", "rejected", "n", "no")


def can_proceed(approval_status: str | None) -> bool:
    """True only for an Approved response.

    Every other value - Pending, Rejected, None, or an unexpected string -
    returns False, so the default answer is always "do not proceed".
    """
    return approval_status == APPROVED


# ---------------------------------------------------------------------------
# 2. REVIEW A GENERATED RESPONSE
# ---------------------------------------------------------------------------
def review_response(response: str, input_func=input, output_func=print) -> str:
    """Show the generated response and ask a human to approve or reject it.

    Returns APPROVED or REJECTED. The question repeats until the reviewer
    types a valid answer, so an invalid choice is never accepted. If the
    reviewer cannot answer at all (end of input, or Ctrl+C), the status stays
    PENDING - the response is never approved automatically.

    ``input_func`` and ``output_func`` default to the built-ins; tests pass
    their own functions so the review can run without a real keyboard.
    """
    output_func("Generated customer response (not yet approved):")
    output_func("-" * 60)
    for line in (response or "").splitlines():
        output_func(f"  {line}")
    output_func("-" * 60)

    while True:
        try:
            answer = input_func(PROMPT)
        except (EOFError, KeyboardInterrupt):
            # No human decision was received: stay Pending, do not proceed.
            output_func("")
            output_func("No reviewer decision was received.")
            output_func("The response stays Pending and cannot proceed.")
            return PENDING

        choice = (answer or "").strip().lower()

        if choice in APPROVE_ANSWERS:
            return APPROVED
        if choice in REJECT_ANSWERS:
            return REJECTED

        # Invalid input is explained and the question is asked again.
        output_func(INVALID_CHOICE_MESSAGE)


# ---------------------------------------------------------------------------
# 3. RECORD THE DECISION (AUDIT LOG)
# ---------------------------------------------------------------------------
def record_approval(response: str, decision: str,
                    customer_message: str = "") -> dict:
    """Store one review decision and return the saved approval record.

    The returned dictionary contains exactly the audit-log columns:
        approval_id, customer_message, generated_response,
        approval_status, reviewed_at

    A decision of anything other than Approved or Rejected is refused with a
    ValueError, so an unvalidated value can never enter the audit log.
    """
    if decision not in REVIEW_DECISIONS:
        raise ValueError(
            f"Invalid approval decision {decision!r}. "
            f"Expected one of: {', '.join(REVIEW_DECISIONS)}."
        )

    # UTC + a fixed format keeps timestamps sortable and unambiguous.
    reviewed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    approval_id = database.save_approval(
        customer_message=customer_message,
        generated_response=response or "",
        approval_status=decision,
        reviewed_at=reviewed_at,
    )

    return {
        "approval_id": approval_id,
        "customer_message": customer_message,
        "generated_response": response or "",
        "approval_status": decision,
        "reviewed_at": reviewed_at,
    }
