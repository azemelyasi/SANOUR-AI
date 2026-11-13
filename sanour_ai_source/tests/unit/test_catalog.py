import unittest

from pydantic import ValidationError

from sanour_ai.models.capabilities import ModelCapabilities, ModelProfile
from sanour_ai.models.catalog import ModelCatalog


def profile(alias: str, **overrides) -> ModelProfile:
    values = {
        "alias": alias,
        "provider": "mock",
        "model_id": f"{alias}-id",
        "capabilities": ModelCapabilities(
            text_generation=True, max_context_tokens=1000, max_output_tokens=500
        ),
    }
    values.update(overrides)
    return ModelProfile(**values)


class ModelCatalogTests(unittest.TestCase):
    def test_valid_catalog_keeps_input_order_and_uses_tuple(self):
        catalog = ModelCatalog(profiles=(profile("b_model"), profile("a_model")))
        self.assertEqual([p.alias for p in catalog.profiles], ["b_model", "a_model"])
        self.assertIsInstance(catalog.profiles, tuple)

    def test_list_and_dict_input_are_validated_as_profiles(self):
        catalog = ModelCatalog(
            profiles=[
                {
                    "alias": "dict_model",
                    "provider": "mock",
                    "model_id": "dict-id",
                    "capabilities": {
                        "text_generation": True,
                        "max_context_tokens": 10,
                        "max_output_tokens": 5,
                    },
                }
            ]
        )
        self.assertIsInstance(catalog.profiles[0], ModelProfile)

    def test_empty_catalog_rejected(self):
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=())
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=[])

    def test_missing_profiles_rejected(self):
        with self.assertRaises(ValidationError):
            ModelCatalog()

    def test_duplicate_aliases_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            ModelCatalog(profiles=(profile("same_model"), profile("same_model")))
        self.assertIn("duplicate model alias: same_model", str(ctx.exception))

    def test_duplicate_reporting_is_deterministic(self):
        messages = set()
        for _ in range(3):
            with self.assertRaises(ValidationError) as ctx:
                ModelCatalog(
                    profiles=(
                        profile("a_model"),
                        profile("b_model"),
                        profile("b_model"),
                        profile("a_model"),
                    )
                )
            messages.add(str(ctx.exception))
        self.assertEqual(len(messages), 1)
        self.assertIn("duplicate model alias: b_model", messages.pop())

    def test_unknown_catalog_field_rejected(self):
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=(profile("a_model"),), default_model="a_model")

    def test_invalid_profile_rejected_by_model_profile_rules(self):
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=({"alias": "BAD ALIAS", "provider": "mock", "model_id": "x",
                                    "capabilities": {"max_context_tokens": 1,
                                                     "max_output_tokens": 1}},))

    def test_profile_missing_required_field_rejected(self):
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=({"alias": "no_model_id", "provider": "mock",
                                    "capabilities": {"max_context_tokens": 1,
                                                     "max_output_tokens": 1}},))

    def test_unknown_field_inside_profile_rejected(self):
        with self.assertRaises(ValidationError):
            ModelCatalog(profiles=({"alias": "extra_field", "provider": "mock", "model_id": "x",
                                    "capabilities": {"max_context_tokens": 1,
                                                     "max_output_tokens": 1},
                                    "api_key": "not-allowed"},))

    def test_catalog_attributes_cannot_be_reassigned(self):
        catalog = ModelCatalog(profiles=(profile("a_model"),))
        with self.assertRaises(ValidationError):
            catalog.profiles = (profile("b_model"),)

    def test_profiles_tuple_has_no_mutating_methods(self):
        catalog = ModelCatalog(profiles=(profile("a_model"),))
        self.assertIsInstance(catalog.profiles, tuple)
        self.assertFalse(hasattr(catalog.profiles, "append"))

    def test_contained_profiles_are_frozen(self):
        catalog = ModelCatalog(profiles=(profile("a_model"),))
        with self.assertRaises(ValidationError):
            catalog.profiles[0].priority = 1
