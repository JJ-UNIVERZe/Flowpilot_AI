# Offline Policy Knowledge Base

The policy retriever is an isolated demo utility; it is not connected to the
FlowPilot workflow. It uses local Markdown files and pure-Python BM25. It does
not create embeddings or make network requests. Retrieved text is data, and a
human reviewer remains responsible for decisions.

## Evaluation

The labelled evaluation contains 25 fictional queries:

| Metric | Result |
|---|---:|
| Top-1 accuracy | 25/25 (100.0%) |
| Top-3 accuracy | 25/25 (100.0%) |
| Failed top-1 queries | 0 |

Run the evaluation from the repository root with:

```text
python scripts/eval_kb.py
```

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
