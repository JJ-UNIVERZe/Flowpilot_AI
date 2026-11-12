"""Measure top-1 and top-3 accuracy for the offline policy retriever."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from knowledge_base import search  # noqa: E402


def main() -> int:
    cases = json.loads((ROOT / "kb" / "eval_queries.json").read_text(encoding="utf-8"))
    top_one = 0
    top_three = 0
    failures: list[dict[str, str]] = []

    for case in cases:
        results = search(case["query"], k=3)
        doc_ids = [passage.doc_id for passage in results]
        expected = case["expected_doc_id"]
        top_one += bool(doc_ids and doc_ids[0] == expected)
        top_three += expected in doc_ids
        if not doc_ids or doc_ids[0] != expected:
            failures.append(
                {
                    "query": case["query"],
                    "expected_doc_id": expected,
                    "top_doc_id": doc_ids[0] if doc_ids else "(no result)",
                }
            )

    total = len(cases)
    print(f"Queries: {total}")
    print(f"Top-1 accuracy: {top_one}/{total} ({top_one / total:.1%})")
    print(f"Top-3 accuracy: {top_three}/{total} ({top_three / total:.1%})")
    print(f"Failed queries ({len(failures)}):")
    if failures:
        for failure in failures:
            print(
                f"- {failure['query']} "
                f"(expected {failure['expected_doc_id']}, got {failure['top_doc_id']})"
            )
    else:
        print("- None")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
