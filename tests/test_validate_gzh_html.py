import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.validate_gzh_html import validate


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class ValidateGzhHtmlTest(unittest.TestCase):
    def test_valid_fragment_passes_strict_mode(self):
        errors, warnings, leaf_count = validate(fixture("valid.html"), strict=True)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])
        self.assertGreater(leaf_count, 0)

    def test_partial_leaf_is_warning_in_legacy_and_error_in_strict_mode(self):
        errors, warnings, _ = validate(fixture("partial-leaf.html"))
        self.assertEqual(errors, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("未被 <span leaf> 包裹", warnings[0])

        errors, warnings, _ = validate(fixture("partial-leaf.html"), strict=True)
        self.assertEqual(warnings, [])
        self.assertTrue(any("未被 <span leaf> 包裹" in item for item in errors))

    def test_code_area_is_exempt_from_leaf_and_half_punctuation_checks(self):
        errors, warnings, _ = validate(fixture("code-area.html"), strict=True)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_event_attribute_is_rejected(self):
        errors, _, _ = validate(fixture("event-attribute.html"), strict=True)
        self.assertTrue(any("事件属性 onerror" in item for item in errors))

    def test_javascript_url_is_rejected(self):
        errors, _, _ = validate(fixture("unsafe-url.html"), strict=True)
        self.assertTrue(any("javascript: URL" in item for item in errors))

    def test_strict_mode_requires_one_section_root(self):
        errors, _, _ = validate(fixture("invalid-root.html"), strict=True)
        self.assertTrue(any("只有一个顶层 <section>" in item for item in errors))

    def test_fail_on_warning_returns_nonzero(self):
        command = [
            sys.executable,
            str(ROOT / "scripts" / "validate_gzh_html.py"),
            "--strict",
            "--fail-on-warning",
            str(FIXTURES / "half-punctuation.html"),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("WARNING", result.stdout)


if __name__ == "__main__":
    unittest.main()
