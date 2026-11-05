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

## OpenAI provider (step 09, offline only)

`src/sanour_ai/providers/openai_provider.py` implements the first real adapter. It is **not wired**
into any composition root and has **not been connected to OpenAI**. Nothing in this step sends a
real request.

- Setup (after approval only): `pip install -e ".[openai]"`, which pins `openai==3.45.0` and
  `httpx2==2.13.1`. Set `OPENAI_API_KEY` in the environment. The key is never read from files or
  logged.
- Responses API with `store=False` and `max_output_tokens` from the call. The SDK is created with
  `max_retries=0`. No retry, no fallback, no automatic model substitution.
- Deadline: only `ModelCall.deadline` is used. It is checked before I/O and again after the
  response. The four timeouts (connect, read, write, pool) are capped by the remaining budget.
- Model ID: the provider never rewrites it. A mismatch or a missing id is rejected by the gateway
  as `PROVIDER_INVALID_RESPONSE`.
- Errors: 401, 403, 404, 429, 5xx, connection, and timeout are mapped to fixed messages. Raw SDK
  text never reaches the caller or the log.
- `PROVIDER_RATE_LIMITED` is defined in `ErrorCode`, but the gateway still reports 429 as
  `PROVIDER_FAILED` (retryable). Surfacing the new code needs a gateway change, which is not in
  this step.

**Known limit (Trickle):** the per-phase httpx timeouts do not guarantee an overall deadline when
data arrives slowly. Network work can continue after the deadline. The gateway returns on time and
discards the late result, but the worker thread and its semaphore slot stay occupied until the SDK
call returns. No cancellation is implemented or claimed.

Tests in `tests/unit/test_openai_provider.py` run offline. Without the SDK installed, the
real-SDK tests are skipped and the rest use a fake client and fake SDK error classes that exist
only in the tests. With `openai==3.45.0` and `httpx2==2.13.1` installed, the real-SDK tests also run
against a test-only mock transport. That is not a connection to OpenAI. No test in this repository
has contacted the OpenAI API.

## OpenAI adapter security boundary

The adapter is an offline, unwired provider. It is not connected to OpenAI, and nothing in this
repository makes a real request.

- **Endpoint pinned:** the adapter always uses `https://api.openai.com/v1`. `OPENAI_BASE_URL` is
  not honored.
- **Fail closed on environment:** the adapter refuses to start if `OPENAI_BASE_URL`,
  `OPENAI_CUSTOM_HEADERS`, `OPENAI_ORG_ID`, or `OPENAI_PROJECT_ID` is set, even if empty. The error
  names the variable and never prints its value.
- **Validation, not a flag:** `validate_client()` runs directly in the constructor, so a subclass
  cannot skip it. It runs again before every request. A client changed after construction is
  refused with a non-retryable `ProviderError` before any request is sent.
- **What validation requires:** the adapter's key (`auth_headers` equals `Bearer <key>`), the
  pinned endpoint, no organization or project, retries disabled, no custom query parameters, and no
  alternate credential (`workload_identity`).
- **Header policy (exact allowlist, SDK 3.45.0):** the only accepted header names are `accept`,
  `content-type`, `user-agent`, `x-stainless-lang`, `x-stainless-package-version`, `x-stainless-os`,
  `x-stainless-arch`, `x-stainless-runtime`, `x-stainless-runtime-version`, `x-stainless-async`, plus
  `openai-organization` and `openai-project` while they are unset. There is no prefix rule.
  Rejected: `Authorization` (always the adapter's key), any organization or project value that is
  set, and any other name, for example `X-Stainless-Probe`, `X-Probe`, or `Host`. This applies to
  both the merged default headers and caller-supplied custom headers.
- **Transport policy:** the only accepted transport class is the httpx2 default transport.
  Rejected: an explicit proxy on the client (`_mounts`), an explicit proxy on the transport, TLS
  verification disabled, environment proxy or certificate trust enabled, and any unknown transport
  type. Unknown transports are refused until a reviewer adds them to the policy.
- **Environment transport:** `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_FILE`, and
  `SSL_CERT_DIR` are ignored (`trust_env=False`). Verified: with `HTTPS_PROXY` set, a plain httpx2
  client routes OpenAI traffic through the proxy, and the adapter's client does not.
- **Logging:** `OPENAI_LOG` is read by the SDK and only changes verbosity. The SDK redacts the
  authorization header. Debug logs on success and error paths were checked for the key. Keep
  `OPENAI_LOG` off in production anyway.

| Variable | Effect on this adapter | Action |
|---|---|---|
| `OPENAI_BASE_URL` | Redirects requests (key sent to host) | Blocked (fail closed) |
| `OPENAI_CUSTOM_HEADERS` | Injects or replaces headers | Blocked |
| `OPENAI_ORG_ID`, `OPENAI_PROJECT_ID` | Adds billing headers | Blocked |
| `OPENAI_ADMIN_KEY` | Populates `admin_api_key`; Responses auth unchanged (tested) | Not blocked |
| `OPENAI_WEBHOOK_SECRET` | Webhook verification only; auth unchanged (tested) | Not blocked |
| `OPENAI_API_TYPE`, `OPENAI_API_VERSION` | Module-level or Azure switches; client stays `openai.OpenAI` (tested) | Not blocked |
| `OPENAI_API_KEY` | Not used; the adapter always passes its own key | Not blocked |
| `OPENAI_LOG` | Log verbosity; authorization header redacted (tested) | Not blocked |
| `HTTP(S)_PROXY`, `ALL_PROXY`, `NO_PROXY`, `SSL_CERT_*` | Route traffic or change trust store | Ignored (`trust_env=False`) |

## Test suite and what it proves

- **SDK-free tests** always run: key handling, environment guard, header allowlist (pure policy),
  and constructor shape.
- **SDK-backed tests** need `openai==3.45.0` and `httpx2==2.13.1` installed. They build real
  `openai.OpenAI` clients on `httpx2.MockTransport`. The mock transport is allowed only inside the
  test suite, through a test-only override of the transport policy. The production policy accepts
  only the httpx2 default transport. These tests make no network connection and use no real key.
- **Real-network tests:** none. No test in this repository has contacted the OpenAI API.
- **Skips:** without the SDK installed, the SDK-backed tests are skipped and reported as skipped.
  They are not counted as passed.
