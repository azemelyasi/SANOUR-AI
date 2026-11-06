import unittest

from sanour_ai.core.request_manager import accept
from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.router import DeterministicRouter, estimate_tokens

RID = "b" * 32


def caps(**overrides):
    base = {"text_generation": True, "max_context_tokens": 1000, "max_output_tokens": 1000}
    base.update(overrides)
    return ModelCapabilities(**base)


def profile(alias, priority=100, runs_locally=True, **cap_overrides):
    return ModelProfile(
        alias=alias,
        provider="mock",
        model_id=f"{alias}-id",
        capabilities=caps(**cap_overrides),
        runs_locally=runs_locally,
        priority=priority,
    )


def req(**overrides):
    raw = {"prompt": "hello world"}
    raw.update(overrides)
    return accept(raw, RID)


class RoutingTests(unittest.TestCase):
    def test_estimate_tokens(self):
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("abcde"), 2)
        self.assertEqual(estimate_tokens(""), 0)

    def test_selects_lowest_priority_then_alias(self):
        router = DeterministicRouter(
            [profile("b_model", 10), profile("a_model", 10), profile("c_model", 5)]
        )
        self.assertEqual(router.select(req()).alias, "c_model")

    def test_tie_breaks_by_alias(self):
        router = DeterministicRouter([profile("b_model", 10), profile("a_model", 10)])
        self.assertEqual(router.select(req()).alias, "a_model")

    def test_same_input_gives_same_choice_regardless_of_input_order(self):
        first = DeterministicRouter([profile("x_model", 1), profile("y_model", 1)])
        second = DeterministicRouter([profile("y_model", 1), profile("x_model", 1)])
        self.assertEqual(first.select(req()).alias, second.select(req()).alias)

    def test_text_generation_is_required(self):
        router = DeterministicRouter([profile("no_text", text_generation=False)])
        with self.assertRaises(SanourError) as ctx:
            router.select(req())
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)

    def test_output_limit_filters_candidates(self):
        router = DeterministicRouter([profile("small_out", max_output_tokens=100)])
        with self.assertRaises(SanourError) as ctx:
            router.select(req(max_output_tokens=200))
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)

    def test_context_limit_filters_candidates(self):
        router = DeterministicRouter([profile("small_ctx", max_context_tokens=30)])
        with self.assertRaises(SanourError):
            router.select(req(prompt="x" * 100, max_output_tokens=50))

    def test_offline_only_excludes_remote_models(self):
        router = DeterministicRouter(
            [profile("remote", priority=1, runs_locally=False), profile("local", priority=2)]
        )
        self.assertEqual(router.select(req(offline_only=True)).alias, "local")

    def test_remote_allowed_when_not_offline_only(self):
        router = DeterministicRouter([profile("remote", runs_locally=False)])
        self.assertEqual(router.select(req()).alias, "remote")

    def test_duplicate_aliases_rejected(self):
        with self.assertRaises(ValueError):
            DeterministicRouter([profile("same_name"), profile("same_name")])

    def test_empty_router_has_no_eligible_model(self):
        with self.assertRaises(SanourError) as ctx:
            DeterministicRouter([]).select(req())
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)
