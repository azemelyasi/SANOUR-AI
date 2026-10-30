# SANOUR AI (Phase 1, remediated)

Minimal orchestration core. Mock provider only. No network, no API keys, no paid services.

## Windows PowerShell (not yet run on Windows)

    py -3.11 -m venv .venv
    .\.venv\Scripts\Activate.ps1
    python -m pip install -e ".[dev]"
    python -m pytest -q
    python -m ruff check .

Standard library only (no pytest needed):

    $env:PYTHONPATH = "src;."
    python -m unittest discover -s tests -t . -v

## Request policy

- **One attempt per request.** The router selects exactly one model. There is no fallback list,
  and `allow_fallback` is rejected as an invalid field.
- **One total deadline.** `timeout_seconds` (default 10, max 60) starts when `handle()` starts.
  Validation, routing, and the provider call all count against it. An expired deadline fails
  before any provider call.
- **Tolerance.** The caller returns at about the deadline. Tests allow 0.25 s of scheduling
  overhead. Measured only in the sandbox, not on Windows.
- **No retries.** `retryable` in an error tells the client whether a later retry may succeed.
  The core never retries itself.

## Timeout and worker policy

- Provider calls run in daemon threads. Python cannot forcibly stop a thread.
- **A timed-out call is not stopped.** It keeps running until the provider returns, and its
  result is discarded. The code makes no claim that the thread was terminated.
- **Bounded accumulation.** A semaphore caps live worker threads, including abandoned ones, at
  `SANOUR_MAX_CONCURRENT_PROVIDER_CALLS` (default 4, valid range 1 to 16).
- **Overflow fails fast.** When the cap is reached, a new call returns `provider_overloaded`
  (retryable) and starts no thread.
- **Slots return only when workers return.** A provider call that never returns keeps its slot
  for the life of the process. Known limit: recovering it requires a restart.
- **No cancellation** is implemented.

## Configuration

`config/settings.py` holds one operational value: the worker cap. It is read from the environment
variable above. No `.env` file is read, so no secrets can enter through it. Invalid values raise
`ConfigError` at startup, and the message names the setting but never its value.

## Layout

- `core/`: SanourCore entry point and request acceptance.
- `models/`: request/response schemas, capabilities, router, gateway, provider protocol.
- `providers/`: deterministic mock provider.
- `config/`: typed runtime settings.
- `security/`: secret redaction.
- `observability/`: redacted audit events (no prompt text).
- `tests/`: unit tests, integration tests, and shared helpers in `support.py`.
