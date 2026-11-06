"""Tests for the OpenAI provider.

Offline only. No network, no real key. Provider tests build real openai.OpenAI clients on
httpx2.MockTransport. Tests that need the SDK or httpx2 skip cleanly when they are not installed.
The SDK-free tests always run.
"""

import importlib.util
import inspect
import json
import logging
import os
import subprocess
import sys
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from pydantic import SecretStr

from sanour_ai.config.settings import ConfigError
from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.base import ModelCall, ModelProvider, ModelResult, ProviderError
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.gateway import ModelGateway
from sanour_ai.providers import openai_provider as op
from sanour_ai.providers.openai_provider import (
    CONNECT_TIMEOUT_S,
    POOL_TIMEOUT_S,
    READ_TIMEOUT_S,
    WRITE_TIMEOUT_S,
    OpenAIProvider,
    RateLimitedError,
    default_sdk_error_types,
    load_api_key,
)

SECRET = "sk-FAKETESTKEY0123456789ABCDEF"  # fake, test-only


def _sdk_available():
    return (
        importlib.util.find_spec("openai") is not None
        and importlib.util.find_spec("httpx2") is not None
    )


SDK_REQUIRED = unittest.skipUnless(_sdk_available(), "openai and httpx2 not installed; skipped")


def tuple_timeout(remaining):
    return (
        min(CONNECT_TIMEOUT_S, remaining),
        min(READ_TIMEOUT_S, remaining),
        min(WRITE_TIMEOUT_S, remaining),
        min(POOL_TIMEOUT_S, remaining),
    )


def _call(model="gpt-test", seconds=10.0):
    return ModelCall(model_id=model, prompt="hi", max_output_tokens=64,
                     deadline=time.monotonic() + seconds)


def _ok_body(model="gpt-test", text="hello"):
    body = {
        "id": "resp_1", "object": "response", "created_at": 0, "status": "completed",
        "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
    }
    if model is not None:
        body["model"] = model
    return body


# ---------------------------------------------------------------- SDK-free tests (always run)


class ApiKeyTests(unittest.TestCase):
    def test_missing_key_raises_without_echo(self):
        with self.assertRaises(ConfigError) as ctx:
            load_api_key({})
        self.assertIn(op.ENV_OPENAI_API_KEY, str(ctx.exception))

    def test_blank_key_rejected(self):
        with self.assertRaises(ConfigError):
            load_api_key({op.ENV_OPENAI_API_KEY: "   "})

    def test_key_read_from_env_and_not_leaked_in_repr(self):
        key = load_api_key({op.ENV_OPENAI_API_KEY: SECRET})
        self.assertIsInstance(key, SecretStr)
        self.assertNotIn(SECRET, repr(key))
        self.assertNotIn(SECRET, str(key))


class EnvironmentGuardTests(unittest.TestCase):
    def test_each_blocked_variable_fails_closed_without_echo(self):
        for name in op.BLOCKED_ENV_VARS:
            with self.subTest(name=name):
                with self.assertRaises(ConfigError) as ctx:
                    op.check_environment({name: "https://attacker.example/v1"})
                self.assertIn(name, str(ctx.exception))
                self.assertNotIn("attacker.example", str(ctx.exception))

    def test_empty_blocked_variable_still_blocks(self):
        with self.assertRaises(ConfigError):
            op.check_environment({"OPENAI_BASE_URL": ""})

    def test_clean_environment_passes(self):
        op.check_environment({"OPENAI_API_KEY": SECRET})

    def test_provider_construction_refuses_blocked_environment(self):
        with mock.patch.dict(os.environ, {"OPENAI_BASE_URL": "https://attacker.example/v1"}):
            with self.assertRaises(ConfigError):
                OpenAIProvider(SecretStr(SECRET), client=object())

    def test_default_builder_checks_before_sdk_is_used(self):
        fake_openai = mock.Mock()
        with mock.patch.dict("sys.modules", {"openai": fake_openai}), mock.patch.dict(
            os.environ, {"OPENAI_CUSTOM_HEADERS": "Authorization: attacker"}
        ):
            with self.assertRaises(ConfigError):
                op._build_default_client(SecretStr(SECRET))
        fake_openai.OpenAI.assert_not_called()


class HeaderAllowlistTests(unittest.TestCase):
    """Pure policy checks. The allowlist is exact: no prefix rules."""

    def test_allowlist_is_exact_sdk_3_45_0_names(self):
        self.assertEqual(
            op.SDK_DEFAULT_HEADER_NAMES,
            frozenset({
                "accept", "content-type", "user-agent",
                "x-stainless-lang", "x-stainless-package-version", "x-stainless-os",
                "x-stainless-arch", "x-stainless-runtime", "x-stainless-runtime-version",
                "x-stainless-async",
            }),
        )
        self.assertFalse(hasattr(op, "SDK_DEFAULT_HEADER_PREFIXES"))

    def test_x_stainless_probe_rejected(self):
        self.assertEqual(op._header_problem("X-Stainless-Probe", "1"), "custom header is set")

    def test_sdk_header_accepted(self):
        self.assertIsNone(op._header_problem("X-Stainless-Lang", "python"))
        self.assertIsNone(op._header_problem("User-Agent", "x"))

    def test_authorization_override_rejected(self):
        self.assertEqual(op._header_problem("Authorization", "Bearer x"),
                         "authorization header override is set")

    def test_org_override_rejected_but_omit_accepted(self):
        self.assertEqual(op._header_problem("OpenAI-Organization", "org-x"),
                         "organization or project header is set")
        self.assertIsNone(op._header_problem("OpenAI-Organization", object()))


class ConstructorPathTests(unittest.TestCase):
    """The validation call is direct in __init__ and cannot be skipped by a subclass."""

    def test_production_constructor_has_no_bypass_parameter(self):
        params = inspect.signature(OpenAIProvider.__init__).parameters
        self.assertEqual(set(params), {"self", "api_key", "client", "errors", "timeout_factory"})

    def test_no_overridable_check_hook_exists(self):
        self.assertFalse(any("check_client" in name for name in dir(OpenAIProvider)))

    def test_init_calls_validation_directly_and_builds_nothing_when_client_given(self):
        client = object()
        placeholder = op.SdkErrorTypes(*([Exception] * 9))  # no SDK import needed
        with mock.patch.object(op, "validate_client") as validate, mock.patch.object(
            op, "_build_default_client"
        ) as build:
            OpenAIProvider(SecretStr(SECRET), client=client, errors=placeholder)
        validate.assert_called_once()
        self.assertIs(validate.call_args.args[0], client)
        build.assert_not_called()

    def test_unknown_client_rejected_by_production_constructor(self):
        with self.assertRaises(ConfigError) as ctx:
            OpenAIProvider(SecretStr(SECRET), client=object())
        self.assertIn("client rejected", str(ctx.exception))
        self.assertNotIn(SECRET, str(ctx.exception))


# ---------------------------------------------------------------- SDK-backed tests


class SdkTestCase(unittest.TestCase):
    """Allows httpx2.MockTransport for the duration of each test. Test-only policy override."""

    def setUp(self):
        import httpx2

        patcher = mock.patch.object(
            op, "_trusted_transport_types",
            return_value=(httpx2.HTTPTransport, httpx2.MockTransport),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in op.BLOCKED_ENV_VARS:
            os.environ.pop(name, None)

    def sdk_client(self, handler, api_key=SECRET):
        import httpx2
        import openai

        self.requests = getattr(self, "requests", [])

        def recording(req):
            self.requests.append(req)
            return handler(req)

        http = httpx2.Client(transport=httpx2.MockTransport(recording), trust_env=False)
        return openai.OpenAI(api_key=api_key, base_url=op.PINNED_BASE_URL, max_retries=0,
                             http_client=http)

    def provider(self, handler, **kwargs):
        client = self.sdk_client(handler)
        return OpenAIProvider(SecretStr(SECRET), client=client,
                              errors=default_sdk_error_types(), timeout_factory=tuple_timeout,
                              **kwargs), client

    def ok(self, text="hello", model="gpt-test"):
        import httpx2

        return lambda req: httpx2.Response(200, json=_ok_body(model=model, text=text))

    def status(self, code, body=None):
        import httpx2

        payload = body or {"error": {"message": "server text", "type": "x", "code": None}}
        return lambda req: httpx2.Response(code, json=payload)


@SDK_REQUIRED
class ProviderBehaviourTests(SdkTestCase):
    def test_provider_satisfies_contract(self):
        provider, _ = self.provider(self.ok())
        self.assertIsInstance(provider, ModelProvider)
        self.assertEqual(provider.name, "openai")

    def test_success_request_shape_and_parsing(self):
        provider, _ = self.provider(self.ok())
        result = provider.generate(_call())
        self.assertEqual(result, ModelResult(text="hello", model_id="gpt-test"))
        self.assertEqual(len(self.requests), 1)
        req = self.requests[0]
        self.assertEqual(req.url.host, "api.openai.com")
        self.assertTrue(req.url.path.endswith("/responses"))
        body = json.loads(req.content)
        self.assertIs(body["store"], False)
        self.assertEqual(body["model"], "gpt-test")
        self.assertEqual(body["max_output_tokens"], 64)
        self.assertEqual(req.headers["authorization"], "Bearer " + SECRET)

    def test_model_mismatch_passed_through_unchanged(self):
        provider, _ = self.provider(self.ok(model="gpt-test-2024-08-06"))
        self.assertEqual(provider.generate(_call()).model_id, "gpt-test-2024-08-06")

    def test_missing_model_becomes_empty(self):
        provider, _ = self.provider(self.ok(model=None))
        self.assertEqual(provider.generate(_call()).model_id, "")

    def test_empty_output_becomes_empty_text(self):
        import httpx2

        body = _ok_body()
        body["output"] = []
        provider, _ = self.provider(lambda req: httpx2.Response(200, json=body))
        self.assertEqual(provider.generate(_call()).text, "")

    def test_expired_deadline_sends_nothing(self):
        provider, _ = self.provider(self.ok())
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(ModelCall(model_id="gpt-test", prompt="x", max_output_tokens=8,
                                        deadline=time.monotonic() - 1))
        self.assertTrue(ctx.exception.retryable)
        self.assertEqual(getattr(self, "requests", []), [])

    def test_missing_deadline_sends_nothing(self):
        provider, _ = self.provider(self.ok())
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(ModelCall(model_id="gpt-test", prompt="x", max_output_tokens=8))
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(getattr(self, "requests", []), [])

    def test_timeouts_capped_by_remaining_budget(self):
        provider, _ = self.provider(self.ok())
        provider.generate(_call(seconds=0.5))
        timeout = self.requests[0].extensions["timeout"]
        for value in timeout.values():
            self.assertLessEqual(value, 0.5)
            self.assertGreater(value, 0)

    def test_configured_timeouts_used_for_large_budget(self):
        provider, _ = self.provider(self.ok())
        provider.generate(_call(seconds=60))
        timeout = self.requests[0].extensions["timeout"]
        self.assertAlmostEqual(timeout["connect"], CONNECT_TIMEOUT_S)
        self.assertAlmostEqual(timeout["pool"], POOL_TIMEOUT_S)
        self.assertLessEqual(timeout["read"], READ_TIMEOUT_S)

    def test_late_response_is_discarded(self):
        import httpx2

        def slow(req):
            time.sleep(0.05)
            return httpx2.Response(200, json=_ok_body())

        provider, _ = self.provider(slow)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(_call(seconds=0.01))
        self.assertTrue(ctx.exception.retryable)

    def test_error_mapping_single_request_each(self):
        import httpx2

        cases = [
            (self.status(401), False, "provider rejected credentials"),
            (self.status(403), False, "provider denied permission"),
            (self.status(404), False, "provider model or endpoint not found"),
            (self.status(429), True, "provider rate limited"),
            (self.status(500), True, "provider server error"),
            (self.status(503), True, "provider server error"),
            (_raising(lambda req: httpx2.ConnectError("refused", request=req)),
             True, "provider connection failed"),
            (_raising(lambda req: httpx2.ReadTimeout("slow", request=req)),
             True, "provider call timed out"),
        ]
        for handler, retryable, message in cases:
            with self.subTest(message=message):
                self.requests = []
                provider, _ = self.provider(handler)
                with self.assertRaises(ProviderError) as ctx:
                    provider.generate(_call())
                self.assertEqual(ctx.exception.retryable, retryable)
                self.assertEqual(str(ctx.exception), message)
                self.assertEqual(len(self.requests), 1, "zero retries: exactly one request")

    def test_rate_limit_maps_to_rate_limited_error(self):
        provider, _ = self.provider(self.status(429))
        with self.assertRaises(RateLimitedError) as ctx:
            provider.generate(_call())
        self.assertEqual(ctx.exception.error_code, "provider_rate_limited")

    def test_unknown_transport_exception_is_not_retryable(self):
        def boom(req):
            raise RuntimeError("boom " + SECRET)

        provider, _ = self.provider(boom)
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(_call())
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(str(ctx.exception), "provider call failed")
        self.assertNotIn(SECRET, str(ctx.exception))

    def test_key_not_leaked_through_error_text(self):
        provider, _ = self.provider(self.status(401, {"error": {"message": "bad " + SECRET,
                                                               "type": "x", "code": None}}))
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(_call())
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, repr(ctx.exception))

    def test_gateway_success_and_invalid_responses(self):
        profile = ModelProfile(
            alias="openai_default", provider="openai", model_id="gpt-test",
            capabilities=ModelCapabilities(text_generation=True, max_context_tokens=8192,
                                           max_output_tokens=4096),
            runs_locally=False,
        )

        def gateway(handler):
            provider, _ = self.provider(handler)
            return ModelGateway({"openai": provider}, max_concurrent_calls=2)

        self.assertEqual(
            gateway(self.ok()).generate(profile, _call(), time.monotonic() + 5).text, "hello")
        with self.assertRaises(SanourError) as ctx:
            gateway(self.ok(model="gpt-other")).generate(profile, _call(), time.monotonic() + 5)
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_INVALID_RESPONSE)
        with self.assertRaises(SanourError) as ctx:
            gateway(self.ok(model=None)).generate(profile, _call(), time.monotonic() + 5)
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_INVALID_RESPONSE)
        with self.assertRaises(SanourError) as ctx:
            gateway(self.status(429)).generate(profile, _call(), time.monotonic() + 5)
        self.assertEqual(ctx.exception.code, ErrorCode.PROVIDER_RATE_LIMITED)


@SDK_REQUIRED
class ClientValidationTests(SdkTestCase):
    """Positive and negative cases for validate_client on real SDK clients."""

    def _rejected(self, client, fragment):
        with self.assertRaises(ConfigError) as ctx:
            OpenAIProvider(SecretStr(SECRET), client=client)
        message = str(ctx.exception)
        self.assertIn(fragment, message)
        self.assertNotIn(SECRET, message)
        self.assertNotIn("attacker.example", message)

    def _sdk(self, **overrides):
        import httpx2
        import openai

        kwargs = dict(api_key=SECRET, base_url=op.PINNED_BASE_URL, max_retries=0,
                      http_client=httpx2.Client(transport=httpx2.MockTransport(self.ok()),
                                                trust_env=False))
        kwargs.update(overrides)
        return openai.OpenAI(**kwargs)

    def test_factory_client_accepted(self):
        # The factory's real httpx2 transport passes validation with no test patch involved.
        OpenAIProvider(SecretStr(SECRET))

    def test_trusted_sdk_client_accepted(self):
        OpenAIProvider(SecretStr(SECRET), client=self._sdk())

    def test_foreign_endpoint_rejected(self):
        self._rejected(self._sdk(base_url="https://attacker.example/v1"),
                       "endpoint is not the pinned endpoint")

    def test_other_api_key_rejected(self):
        self._rejected(self._sdk(api_key="sk-OTHER-SCRATCH-000"), "api key does not match")

    def test_authorization_default_header_rejected(self):
        self._rejected(self._sdk(default_headers={"Authorization": "Bearer sk-ATTACKER-000"}),
                       "authorization header override is set")

    def test_organization_or_project_set_rejected(self):
        self._rejected(self._sdk(organization="org-evil"), "organization or project is set")

    def test_organization_header_override_rejected(self):
        self._rejected(self._sdk(default_headers={"OpenAI-Organization": "org-x"}),
                       "organization or project header is set")

    def test_project_header_override_rejected(self):
        self._rejected(self._sdk(default_headers={"OpenAI-Project": "proj-x"}),
                       "organization or project header is set")

    def test_custom_default_header_rejected(self):
        self._rejected(self._sdk(default_headers={"X-Probe": "1"}), "custom header is set")

    def test_x_stainless_probe_rejected(self):
        self._rejected(self._sdk(default_headers={"X-Stainless-Probe": "1"}), "custom header is set")

    def test_host_header_override_rejected(self):
        self._rejected(self._sdk(default_headers={"Host": "attacker.example"}), "custom header is set")

    def test_custom_header_collection_rejected(self):
        client = self._sdk()
        client._custom_headers = {"X-Probe": "1"}
        self._rejected(client, "custom header is set")

    def test_custom_query_rejected(self):
        client = self._sdk()
        with mock.patch.object(type(client), "default_query", new_callable=mock.PropertyMock,
                               return_value={"api-version": "2024-01-01"}):
            self._rejected(client, "custom query parameters are set")

    def test_auth_headers_mismatch_rejected(self):
        client = self._sdk()
        with mock.patch.object(type(client), "auth_headers", new_callable=mock.PropertyMock,
                               return_value={"Authorization": "Bearer sk-OTHER-SCRATCH-000"}):
            self._rejected(client, "authorization does not come from the adapter key")

    def test_sdk_default_headers_accepted(self):
        OpenAIProvider(SecretStr(SECRET), client=self._sdk(
            default_headers={"User-Agent": "sanour-test", "X-Stainless-Lang": "python"}))

    def test_retries_enabled_rejected(self):
        self._rejected(self._sdk(max_retries=2), "retries are not disabled")

    def test_env_transport_client_rejected(self):
        import httpx2
        import openai

        client = openai.OpenAI(api_key=SECRET, base_url=op.PINNED_BASE_URL, max_retries=0,
                               http_client=httpx2.Client(trust_env=True))
        self._rejected(client, "environment proxy or TLS settings are enabled")

    def test_explicit_proxy_on_client_rejected(self):
        import httpx2
        import openai

        http = httpx2.Client(trust_env=False, proxy="http://proxy.attacker.example:8080")
        client = openai.OpenAI(api_key=SECRET, base_url=op.PINNED_BASE_URL, max_retries=0,
                               http_client=http)
        self._rejected(client, "explicit proxy or per-host transport is configured")

    def test_explicit_proxy_on_transport_rejected(self):
        import httpx2
        import openai

        transport = httpx2.HTTPTransport(proxy="http://proxy.attacker.example:8080")
        client = openai.OpenAI(api_key=SECRET, base_url=op.PINNED_BASE_URL, max_retries=0,
                               http_client=httpx2.Client(transport=transport, trust_env=False))
        self._rejected(client, "transport uses an explicit proxy")

    def test_tls_verification_disabled_rejected(self):
        import httpx2
        import openai

        transport = httpx2.HTTPTransport(verify=False)
        client = openai.OpenAI(api_key=SECRET, base_url=op.PINNED_BASE_URL, max_retries=0,
                               http_client=httpx2.Client(transport=transport, trust_env=False))
        self._rejected(client, "TLS verification is disabled")

    def test_unknown_transport_type_rejected_under_production_policy(self):
        import httpx2

        with mock.patch.object(op, "_trusted_transport_types",
                               return_value=(httpx2.HTTPTransport,)):
            self._rejected(self._sdk(), "transport type is not trusted")


@SDK_REQUIRED
class RevalidationTests(SdkTestCase):
    """Changes after construction are caught before every request."""

    def _assert_refused(self, provider):
        before = len(getattr(self, "requests", []))
        with self.assertRaises(ProviderError) as ctx:
            provider.generate(_call())
        self.assertFalse(ctx.exception.retryable)
        self.assertEqual(str(ctx.exception), "client configuration changed; request refused")
        self.assertEqual(len(getattr(self, "requests", [])), before, "no request may be sent")

    def test_custom_header_added_after_construction_refused(self):
        provider, client = self.provider(self.ok())
        provider.generate(_call())
        client._custom_headers = {"X-Probe": "1"}
        self._assert_refused(provider)

    def test_api_key_changed_after_construction_refused(self):
        provider, client = self.provider(self.ok())
        client.api_key = "sk-SWAPPED-SCRATCH-000"
        self._assert_refused(provider)

    def test_trust_env_enabled_after_construction_refused(self):
        provider, client = self.provider(self.ok())
        client._client._trust_env = True
        self._assert_refused(provider)

    def test_transport_swapped_after_construction_refused(self):
        import httpx2

        provider, client = self.provider(self.ok())
        client._client._transport = httpx2.HTTPTransport(verify=False)
        self._assert_refused(provider)

    def test_proxy_mount_added_after_construction_refused(self):
        import httpx2

        provider, client = self.provider(self.ok())
        client._client._mounts = {"all://": httpx2.HTTPTransport(proxy="http://proxy.example:8080")}
        self._assert_refused(provider)


@SDK_REQUIRED
class SdkSecurityTests(SdkTestCase):
    def test_factory_client_pins_endpoint_without_org_or_project(self):
        client = op._build_default_client(SecretStr(SECRET))
        self.assertEqual(str(client.base_url).rstrip("/"), op.PINNED_BASE_URL)
        self.assertIsNone(client.organization)
        self.assertIsNone(client.project)
        self.assertEqual(client.max_retries, 0)

    def test_admin_key_env_does_not_replace_authorization(self):
        os.environ["OPENAI_ADMIN_KEY"] = "sk-ADMIN-SCRATCH-000"
        provider, _ = self.provider(self.ok())
        provider.generate(_call())
        self.assertEqual(self.requests[0].headers["authorization"], "Bearer " + SECRET)

    def test_webhook_secret_does_not_change_authorization(self):
        os.environ["OPENAI_WEBHOOK_SECRET"] = "whsec-SCRATCH-000"
        client = op._build_default_client(SecretStr(SECRET))
        self.assertEqual(client.auth_headers, {"Authorization": "Bearer " + SECRET})

    def test_api_type_and_version_do_not_change_client_class_or_endpoint(self):
        import openai

        os.environ["OPENAI_API_TYPE"] = "azure"
        os.environ["OPENAI_API_VERSION"] = "2024-01-01"
        client = op._build_default_client(SecretStr(SECRET))
        self.assertIs(type(client), openai.OpenAI)
        self.assertEqual(str(client.base_url).rstrip("/"), op.PINNED_BASE_URL)

    def test_success_path_logs_contain_no_key_at_debug_level(self):
        provider, _ = self.provider(self.ok())
        records = _capture_openai_logs(lambda: provider.generate(_call()))
        self.assertGreater(len(records), 0, "debug logging produced no records; test is vacuous")
        self.assertFalse(any(SECRET in r for r in records))

    def test_error_path_logs_contain_no_key_at_debug_level(self):
        import httpx2

        handler = lambda req: httpx2.Response(  # noqa: E731
            401, json={"error": {"message": "bad key " + SECRET, "type": "x", "code": None}})
        provider, _ = self.provider(handler)
        records = _capture_openai_logs(lambda: self._expect_error(provider))
        self.assertGreater(len(records), 0, "debug logging produced no records; test is vacuous")
        self.assertFalse(any(SECRET in r for r in records))

    def _expect_error(self, provider):
        with self.assertRaises(ProviderError):
            provider.generate(_call())

    def test_subprocess_openai_log_debug_leaks_no_key(self):
        env = {k: v for k, v in os.environ.items() if k not in op.BLOCKED_ENV_VARS}
        env["OPENAI_LOG"] = "debug"
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
        script = (
            "import httpx2, openai, time\n"
            "from pydantic import SecretStr\n"
            "import sanour_ai.providers.openai_provider as op\n"
            "from sanour_ai.models.base import ModelCall, ProviderError\n"
            f"KEY = {SECRET!r}\n"
            "op._trusted_transport_types = lambda: (httpx2.HTTPTransport, httpx2.MockTransport)\n"
            "def h(req):\n"
            "    return httpx2.Response(401, json={'error': {'message': 'bad ' + KEY,"
            " 'type': 'x', 'code': None}})\n"
            "http = httpx2.Client(transport=httpx2.MockTransport(h), trust_env=False)\n"
            "c = openai.OpenAI(api_key=KEY, base_url=op.PINNED_BASE_URL, max_retries=0,"
            " http_client=http)\n"
            "p = op.OpenAIProvider(SecretStr(KEY), client=c)\n"
            "try:\n"
            "    p.generate(ModelCall(model_id='gpt-test', prompt='x', max_output_tokens=8,\n"
            "                          deadline=time.monotonic() + 10))\n"
            "except ProviderError:\n"
            "    pass\n"
        )
        proc = subprocess.run([sys.executable, "-c", script], env=env, capture_output=True,
                              text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertNotIn(SECRET, proc.stdout + proc.stderr)
        self.assertIn("DEBUG", proc.stderr, "OPENAI_LOG=debug produced no debug output")


@SDK_REQUIRED
class ProxyAndTlsEnvironmentTests(SdkTestCase):
    """Verified against the bundled httpx2 2.13.1 transport internals (pinned version only)."""

    PROXY = "http://proxy.attacker.example:8080"

    def setUp(self):
        super().setUp()
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "SSL_CERT_FILE",
                     "SSL_CERT_DIR"):
            os.environ.pop(name, None)

    def _proxy_url(self, client):
        from httpx2 import URL

        transport = client._client._transport_for_url(URL("https://api.openai.com/v1/responses"))
        return getattr(getattr(transport, "_pool", None), "_proxy_url", None)

    def test_negative_control_plain_client_does_route_via_env_proxy(self):
        import httpx2

        os.environ["HTTPS_PROXY"] = self.PROXY
        transport = httpx2.Client()._transport_for_url(
            httpx2.URL("https://api.openai.com/v1/responses"))
        self.assertIsNotNone(getattr(getattr(transport, "_pool", None), "_proxy_url", None),
                             "control failed: the probe cannot see env proxy routing")

    def test_factory_client_ignores_https_and_all_proxy(self):
        os.environ["HTTPS_PROXY"] = self.PROXY
        os.environ["ALL_PROXY"] = self.PROXY
        client = op._build_default_client(SecretStr(SECRET))
        self.assertIsNone(self._proxy_url(client))
        self.assertIs(client._client._trust_env, False)
        OpenAIProvider(SecretStr(SECRET), client=client)  # still passes validation

    def test_factory_client_ignores_missing_ssl_cert_file(self):
        os.environ["SSL_CERT_FILE"] = "/nonexistent/attacker-ca.pem"
        os.environ["SSL_CERT_DIR"] = "/nonexistent/attacker-ca-dir"
        client = op._build_default_client(SecretStr(SECRET))
        self.assertEqual(str(client.base_url).rstrip("/"), op.PINNED_BASE_URL)


# ---------------------------------------------------------------- helpers


def _raising(make_exc):
    def handler(req):
        raise make_exc(req)

    return handler


def _capture_openai_logs(action):
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Collect(level=logging.DEBUG)
    logger = logging.getLogger("openai")
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        action()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
    return records


class DeadlineAndRateLimitStaticTests(unittest.TestCase):
    def test_rate_limited_error_code_attribute(self):
        self.assertEqual(RateLimitedError("x", retryable=True).error_code, "provider_rate_limited")

    def test_error_code_enum_contains_rate_limited(self):
        self.assertIn("PROVIDER_RATE_LIMITED", ErrorCode.__members__)


class TimeoutTypeTests(unittest.TestCase):
    def test_default_timeout_uses_httpx2_with_capped_values(self):
        fake_httpx2 = mock.Mock()
        with mock.patch.dict(sys.modules, {"httpx2": fake_httpx2}):
            op._default_timeout(0.5)
        fake_httpx2.Timeout.assert_called_once_with(connect=0.5, read=0.5, write=0.5, pool=0.5)

    @unittest.skipUnless(_sdk_available(), "httpx2 not installed; real-type check skipped")
    def test_real_httpx2_timeout_type(self):
        import httpx2

        value = op._default_timeout(0.5)
        self.assertIsInstance(value, httpx2.Timeout)
        self.assertLessEqual(value.read, 0.5)


if __name__ == "__main__":
    unittest.main()
