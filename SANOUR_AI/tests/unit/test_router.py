import unittest

from sanour_ai.core.request_manager import accept
from sanour_ai.errors import ErrorCode, SanourError
from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.router import DeterministicRouter

RID = "c" * 32


def profile(alias, priority=100, runs_locally=True, text=True, max_output=1000, **flags):
    capabilities = ModelCapabilities(
        text_generation=text,
        max_context_tokens=1000,
        max_output_tokens=max_output,
        **flags,
    )
    return ModelProfile(
        alias=alias,
        provider="mock",
        model_id=f"{alias}-id",
        capabilities=capabilities,
        runs_locally=runs_locally,
        priority=priority,
    )


def req(**overrides):
    raw = {"prompt": "hello world"}
    raw.update(overrides)
    return accept(raw, RID)


class RequiredCapabilityRoutingTests(unittest.TestCase):
    def test_selects_profile_satisfying_all_requirements(self):
        router = DeterministicRouter(
            [
                profile("streaming_only", 1, streaming=True),
                profile("both", 50, streaming=True, tool_calling=True),
            ]
        )
        chosen = router.select(req(required_capabilities=["streaming", "tool_calling"]))
        self.assertEqual(chosen.alias, "both")

    def test_rejects_profile_missing_one_requirement(self):
        router = DeterministicRouter([profile("streaming_only", streaming=True)])
        with self.assertRaises(SanourError) as ctx:
            router.select(req(required_capabilities=["streaming", "tool_calling"]))
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)

    def test_skips_higher_priority_incompatible_profile(self):
        router = DeterministicRouter(
            [profile("plain", 1), profile("streamer", 2, streaming=True)]
        )
        self.assertEqual(
            router.select(req(required_capabilities=["streaming"])).alias, "streamer"
        )

    def test_no_compatible_model_returns_existing_safe_error(self):
        router = DeterministicRouter([profile("plain", 1)])
        with self.assertRaises(SanourError) as ctx:
            router.select(req(required_capabilities=["image_input"]))
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)
        self.assertEqual(
            ctx.exception.message, "no configured model can handle this request"
        )
        self.assertFalse(ctx.exception.retryable)

    def test_equal_priority_ties_break_by_alias_regardless_of_input_order(self):
        first = DeterministicRouter(
            [profile("b_model", 10, streaming=True), profile("a_model", 10, streaming=True)]
        )
        second = DeterministicRouter(
            [profile("a_model", 10, streaming=True), profile("b_model", 10, streaming=True)]
        )
        request = req(required_capabilities=["streaming"])
        self.assertEqual(first.select(request).alias, "a_model")
        self.assertEqual(second.select(request).alias, "a_model")

    def test_repeated_calls_return_same_result(self):
        router = DeterministicRouter(
            [profile("x_model", 5, streaming=True), profile("y_model", 1, streaming=True)]
        )
        request = req(required_capabilities=["streaming"])
        self.assertEqual({router.select(request).alias for _ in range(20)}, {"y_model"})

    def test_legacy_routing_without_requirements_is_unchanged(self):
        router = DeterministicRouter(
            [
                profile("zeta", 2),
                profile("alpha", 1, streaming=True),  # fits: chosen by priority then alias
                profile("beta", 1),
                profile("no_text", 0, text=False),  # excluded by text_generation rule
                profile("tiny", 0, max_output=100),  # excluded by output limit (512 default)
            ]
        )
        self.assertEqual(router.select(req()).alias, "alpha")

    def test_text_generation_still_required_with_requirements(self):
        router = DeterministicRouter([profile("no_text", text=False, streaming=True)])
        with self.assertRaises(SanourError) as ctx:
            router.select(req(required_capabilities=["streaming"]))
        self.assertEqual(ctx.exception.code, ErrorCode.NO_ELIGIBLE_MODEL)

    def test_fit_rules_still_apply_with_requirements(self):
        router = DeterministicRouter([profile("tiny", max_output=100, streaming=True)])
        with self.assertRaises(SanourError):
            router.select(req(required_capabilities=["streaming"]))

    def test_offline_rule_still_applies_with_requirements(self):
        router = DeterministicRouter([profile("remote", runs_locally=False, streaming=True)])
        with self.assertRaises(SanourError):
            router.select(req(required_capabilities=["streaming"], offline_only=True))
