# Offline Policy Knowledge Base

The policy retriever supplies local fictional Markdown passages to General
Inquiry replies. It uses pure-Python BM25 and does not create embeddings or
make network requests. Retrieved text is data, never instructions. Unsupported
policy numbers and promises are rejected in generated replies; a human
reviewer remains responsible for decisions.

## Evaluation

The labelled evaluation contains 25 fictional queries:

| Metric | Result |
|---|---:|
| Top-1 accuracy | 25/25 (100.0%) |
| Top-3 accuracy | 25/25 (100.0%) |
| Failed top-1 queries | 0 |

The workflow evaluation also contains 15 labelled General Inquiry policy
cases; the latest measured retrieval accuracy is **100.0% (15/15)**. Overall
workflow classification accuracy was **100.0% (86/86)**, above the existing
98% baseline.

Run the evaluation from the repository root with:

```text
python scripts/eval_kb.py
```

The workflow evaluation also measures labelled General Inquiry policy
retrieval; run it with `python scripts/evaluate.py --mode template`.

## Adding a policy document

1. Add a UTF-8 `.md` file under `kb/`; its filename stem becomes the `doc_id`.
2. Keep the first line exactly `FICTIONAL DEMO POLICY. Not a real company.`,
   then add a descriptive `#` title and 3-6 short `##` sections.
3. Use concrete fictional facts, avoid real brands, and phrase every policy as
   guidance for human review rather than an automatic decision or guarantee.
   Keep each file below 3 KB; files over 100 KB are skipped by the loader.
4. Add representative `query` and `expected_doc_id` entries to
   `kb/eval_queries.json`, then run `python scripts/eval_kb.py`.

The default document directory is `kb/`. Pass `folder=` to `search()` or set
`KB_PATH` to use another directory. The index is cached per directory; call
`reset_index_cache()` after changing documents in a running process.
