from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
from pathlib import Path

from tools.okf_validate import main, validate_bundle


class OkfValidateTest(unittest.TestCase):
    def test_valid_bundle_accepts_minimal_unknown_and_nested_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n* [Minimal](minimal.md) - Small example.\n",
                encoding="utf-8",
            )
            (root / "log.md").write_text(
                "# Bundle Log\n\n"
                "## 2026-07-02\n\n* Updated metadata.\n\n"
                "## 2026-07-01\n\n* Created bundle.\n",
                encoding="utf-8",
            )
            (root / "minimal.md").write_text(
                "---\n"
                "type: Unregistered Producer Type\n"
                "title: Minimal\n"
                "description: Small example.\n"
                "generated: {by: process:test, at: 2026-07-01T00:00:00Z}\n"
                "verified: {by: human:reviewer, at: 2026-07-02T00:00:00Z}\n"
                "sources:\n"
                "  - resource: https://example.com\n"
                "    extra: {nested: [one, two]}\n"
                "status: stable\n"
                "stale_after: 2027-01-01\n"
                "producer_field: {anything: true}\n"
                "---\n"
                "# Minimal\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))

            self.assertEqual(findings, [])

    def test_reports_blocking_conformance_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "nested").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: 0.1\nextra: true\n---\n# Index\n",
                encoding="utf-8",
            )
            (root / "log.md").write_text(
                "# Log\n\n## July 2\n\n* Bad heading.\n",
                encoding="utf-8",
            )
            (root / "nested" / "index.md").write_text(
                "---\ntitle: Not allowed\n---\n# Nested\n",
                encoding="utf-8",
            )
            (root / "missing.md").write_text("# No front matter\n", encoding="utf-8")
            (root / "bad.md").write_text(
                "---\n"
                "type: ''\n"
                "generated: {by: nobody, at: yesterday}\n"
                "verified: [bad]\n"
                "sources: [{title: Missing resource}]\n"
                "status: unknown\n"
                "stale_after: tomorrow\n"
                "---\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "ERROR"
            }

            self.assertIn("okf_version", subjects)
            self.assertIn("frontmatter", subjects)
            self.assertIn("type", subjects)
            self.assertIn("generated.by", subjects)
            self.assertIn("generated.at", subjects)
            self.assertIn("verified[0]", subjects)
            self.assertIn("sources[0].resource", subjects)
            self.assertIn("status", subjects)
            self.assertIn("stale_after", subjects)
            self.assertIn("heading:July 2", subjects)

    def test_advisories_warn_without_failing_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n# Empty Index\n", encoding="utf-8"
            )
            (root / "orphan.md").write_text(
                "---\n"
                "type: Minimal\n"
                "timestamp: 2020-01-01\n"
                "stale_after: 2026-01-01\n"
                "tags: [Needs Fix]\n"
                "---\n"
                "See [missing](missing.md).\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertIn("title", warning_subjects)
            self.assertIn("description", warning_subjects)
            self.assertIn("timestamp", warning_subjects)
            self.assertIn("stale_after", warning_subjects)
            self.assertIn("link:missing.md", warning_subjects)
            self.assertIn("index", warning_subjects)
            self.assertIn("tag:Needs Fix", warning_subjects)
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(["--src", str(root)]), 0)
                self.assertEqual(
                    main(["--src", str(root), "--warnings-as-errors"]),
                    1,
                )

    def test_root_index_without_front_matter_reports_missing_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text("# Undeclared Bundle\n", encoding="utf-8")
            (root / "log.md").write_text(
                "# Bundle Log\n\n## 2026-07-01\n\n* Created.\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            errors = {
                finding.subject for finding in findings if finding.severity == "ERROR"
            }

            self.assertIn("okf_version", errors)


if __name__ == "__main__":
    unittest.main()
