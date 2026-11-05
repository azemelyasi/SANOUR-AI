import pathlib
import unittest

import sanour_ai

SRC = pathlib.Path(sanour_ai.__file__).resolve().parent


class MockIsolationTests(unittest.TestCase):
    """The production package must not contain or import the test mock."""

    def test_src_has_no_mock_module(self):
        self.assertFalse((SRC / "providers" / "mock.py").exists())

    def test_src_never_references_mock(self):
        offenders = []
        for path in SRC.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            for needle in ("providers.mock", "MockProvider", "MockBehavior", "build_mock_core"):
                if needle in text:
                    offenders.append(f"{path.relative_to(SRC)}: {needle}")
        self.assertEqual(offenders, [])

    def test_core_module_does_not_expose_builder(self):
        from sanour_ai.core import sanour_core

        self.assertFalse(hasattr(sanour_core, "build_mock_core"))


if __name__ == "__main__":
    unittest.main()
