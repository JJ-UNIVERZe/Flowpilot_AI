"""Manually verify one real OpenAI response; never used by automated tests."""

import argparse
import os
import sys

from llm_response_generator import get_openai_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Send one explicitly confirmed live OpenAI verification request."
    )
    parser.add_argument(
        "--confirm-live-call",
        action="store_true",
        help="Confirm that one billable request will be sent to OpenAI.",
    )
    options = parser.parse_args(argv)
    if not options.confirm_live_call:
        parser.error("Pass --confirm-live-call to permit a real API request.")

    if not os.getenv("OPENAI_API_KEY"):
        print("Live verification requires OPENAI_API_KEY; no request was sent.")
        return 2

    provider = get_openai_provider()
    if provider is None:
        print("OpenAI provider is unavailable; no request was sent.")
        return 2

    response = provider.generate_messages([
        {
            "role": "system",
            "content": "Reply with the exact text: FlowPilot live verification passed.",
        },
        {
            "role": "user",
            "content": "Return the exact verification text requested.",
        },
    ])
    if response != "FlowPilot live verification passed.":
        print("Live verification failed: no expected response was received.")
        return 1

    print(
        "Live verification succeeded for model "
        f"{os.getenv('OPENAI_MODEL', 'gpt-4o-mini')}; response content was not logged."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
