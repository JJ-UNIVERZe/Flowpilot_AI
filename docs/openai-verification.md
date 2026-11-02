# Manual OpenAI verification

Live verification is **UNVERIFIED**. Automated tests use mocked providers and
must never send OpenAI requests.

The manual verifier sends exactly one real, potentially billable request. It
does not send customer or order data and does not print the API key or response
body. Do not run it unless you intentionally want to make that request.

1. Install the project requirements in the active Python environment.
2. Provide `OPENAI_API_KEY` through your operating system or IDE's secure
   environment configuration. Do not put a real key in `.env.example`, source
   files, command history, logs, or Git.
3. Optionally set `OPENAI_MODEL`; otherwise the SDK provider's default model is
   `gpt-4o-mini`.
4. From the project root, explicitly confirm the live call:

   ```text
   python -m scripts.verify_openai_live --confirm-live-call
   ```

Without the confirmation flag, the verifier exits before checking the key or
constructing a provider. With no key, it exits without sending a request. A
successful result means the provider returned the expected fixed verification
text; it does not certify factual validation, application availability, or
production readiness.
