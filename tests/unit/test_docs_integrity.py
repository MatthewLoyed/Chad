"""Pure stdlib fixtures: run with unittest, without application/conftest setup."""
import tempfile
from pathlib import Path
import unittest

from scripts.dev.check_docs import anchors, check_roster, destinations, duplicate_warnings, link_defects


class DocsIntegrityTests(unittest.TestCase):
    def test_headings_unicode_duplicates_and_code_examples(self):
        self.assertEqual(anchors("# Hello, `World`!\n## Hello, World!\n# Café\n"
                                 "```md\n# Example\n```\nTitle\n---"),
                         {"hello-world", "hello-world-1", "café", "title"})

    def test_link_forms_and_code_are_distinct(self):
        text = ('[plain](one.md#hello) ![image](<a b.png>) [file](x(y).md)\n'
                '[ref][Key]\n[key]: two.md\n`[code](missing)`\n~~~\n[x](absent)\n~~~')
        self.assertEqual(set(destinations(text)), {"one.md#hello", "a b.png", "x(y).md", "two.md"})

    def test_missing_paths_anchors_and_external_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "target.md").write_text("# Valid\n", encoding="utf-8")
            (root / "page.md").write_text(
                "[ok](target.md#valid) [missing](absent.md) [anchor](target.md#bad) "
                "[external](https://example.invalid/no-network) [escape](../outside.md)", encoding="utf-8")
            errors = link_defects(root, Path("page.md"))
            self.assertEqual(len(errors), 3)
            self.assertTrue(any("missing path: absent.md" in e for e in errors))
            self.assertTrue(any("missing heading" in e for e in errors))
            self.assertTrue(any("outside repository" in e for e in errors))

    def test_roster_checks_source_without_importing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "src/core/orchestration/agents/factory.py"
            page = root / "docs/architecture/HIERARCHICAL_AGENTS.md"
            source.parent.mkdir(parents=True)
            page.parent.mkdir(parents=True)
            source.write_text("raise RuntimeError('must not import')\nAGENT_CONFIGS = {'agent': {'domain': 'files'}}")
            page.write_text("<!-- agent-roster:start -->\nstale\n<!-- agent-roster:end -->")
            self.assertTrue(check_roster(root))
            self.assertFalse(check_roster(root, write=True))
            self.assertFalse(check_roster(root))
            self.assertIn("`agent` | `files`", page.read_text())

    def test_duplicate_warning_excludes_examples(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paragraph = "A meaningful repeated paragraph. " * 12
            for name in ("a.md", "b.md"):
                (root / name).write_text(paragraph + "\n\n```\n" + paragraph + "\n```", encoding="utf-8")
            self.assertEqual(len(duplicate_warnings(root, [Path("a.md"), Path("b.md")])), 1)


if __name__ == "__main__":
    unittest.main()
