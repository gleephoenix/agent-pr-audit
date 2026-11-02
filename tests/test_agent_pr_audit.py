"""Offline unit tests for agent_pr_audit (no network)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import agent_pr_audit as apa  # noqa: E402


class ClassifyTests(unittest.TestCase):
    def test_tests_paths(self):
        self.assertIn("tests", apa.classify(["tests/unit/test_x.py"]))
        self.assertIn("tests", apa.classify(["src/__tests__/a.ts"]))
        self.assertIn("tests", apa.classify(["pkg/foo_test.go"]))

    def test_ci_and_lint(self):
        cats = apa.classify([".github/workflows/test.yml", "pyproject.toml"])
        self.assertIn("ci_workflow", cats)
        self.assertIn("lint_config", cats)

    def test_deps_and_secrets(self):
        self.assertIn("deps", apa.classify(["package-lock.json"]))
        self.assertIn("secrets", apa.classify([".env.local"]))

    def test_ordinary_source_not_guardrail(self):
        cats = apa.classify(["src/index.ts", "README.md"])
        self.assertFalse(cats & apa.GUARD)


class PatchTests(unittest.TestCase):
    def test_added_deleted(self):
        patch = "@@ -1,2 +1,3 @@\n-old\n+new\n+another\n context\n-removed\n"
        a, d = apa.parse_patch(patch)
        self.assertEqual(a, ["new", "another"])
        self.assertEqual(d, ["old", "removed"])

    def test_headers_ignored(self):
        a, d = apa.parse_patch("--- a/x\n+++ b/x\n+real\n")
        self.assertEqual(a, ["real"])


class MarkerTests(unittest.TestCase):
    def _labels(self, text):
        return [label for label, rx in apa.WEAKEN if rx.search(text)]

    def test_real_bypasses_flagged(self):
        self.assertIn("skipped test", self._labels("    pytest.skip('later')"))
        self.assertIn("CI continue-on-error", self._labels("continue-on-error: true"))
        self.assertIn("CI force-true / no-verify", self._labels("run: pytest || true"))

    def test_waitforexit_is_not_a_skip(self):
        # Regression: `WaitForExit(` used to match the `xit(` skip pattern.
        self.assertEqual(self._labels("if (-not $process.WaitForExit(300000)) {"), [])

    def test_exit_zero_boundary(self):
        # A string literal that mentions `exit 0` is not a shell bypass.
        self.assertEqual(self._labels("print('exit 0 is fine')"), [])
        # A real shell exit-zero still is.
        self.assertIn("CI force-true / no-verify", self._labels("    exit 0"))
        self.assertIn("CI force-true / no-verify", self._labels("command || true"))


class SamplingTests(unittest.TestCase):
    def test_even_sample_keeps_order_and_size(self):
        items = list(range(1000))
        out = apa.even_sample(items, 120)
        self.assertEqual(len(out), 120)
        self.assertEqual(out, sorted(out))
        self.assertLess(out[0], out[-1])

    def test_small_list_unchanged(self):
        items = [1, 2, 3]
        self.assertEqual(apa.even_sample(items, 120), items)


if __name__ == "__main__":
    unittest.main()
