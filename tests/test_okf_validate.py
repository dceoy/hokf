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
            error_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "ERROR"
            }
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertIn("okf_version", error_subjects)
            self.assertIn("frontmatter", error_subjects)
            self.assertIn("type", error_subjects)
            self.assertIn("heading:July 2", error_subjects)

            # Optional metadata (OKF v0.2 §5-10) is advisory, not blocking.
            self.assertIn("generated.by", warning_subjects)
            self.assertIn("generated.at", warning_subjects)
            self.assertIn("verified[0]", warning_subjects)
            self.assertIn("sources[0].resource", warning_subjects)
            self.assertIn("status", warning_subjects)
            self.assertIn("stale_after", warning_subjects)

    def test_reserved_headings_inside_literal_blocks_do_not_satisfy_structure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "```markdown\n"
                "# Example Index\n"
                "```\n",
                encoding="utf-8",
            )
            (root / "log.md").write_text(
                "    # Example Log\n"
                "    ## 2026-07-03\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            error_keys = {
                (finding.path, finding.subject)
                for finding in findings
                if finding.severity == "ERROR"
            }

            self.assertIn(("index.md", "body"), error_keys)
            self.assertIn(("log.md", "body"), error_keys)
            self.assertIn(("log.md", "date headings"), error_keys)

    def test_reserved_headings_inside_raw_html_do_not_satisfy_structure(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "<div>\n# Example Index\n</div>\n",
                encoding="utf-8",
            )
            (root / "log.md").write_text(
                "<div>\n"
                "# Example Log\n"
                "## 2026-07-03\n"
                "</div>\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            error_keys = {
                (finding.path, finding.subject)
                for finding in findings
                if finding.severity == "ERROR"
            }

            self.assertIn(("index.md", "body"), error_keys)
            self.assertIn(("log.md", "body"), error_keys)
            self.assertIn(("log.md", "date headings"), error_keys)

    def test_log_title_must_be_the_first_content_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text("# Index\n", encoding="utf-8")
            (root / "log.md").write_text(
                "Introductory prose.\n\n"
                "# Bundle Log\n\n"
                "## 2026-07-03\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            error_keys = {
                (finding.path, finding.subject)
                for finding in findings
                if finding.severity == "ERROR"
            }

            self.assertIn(("log.md", "body"), error_keys)
            self.assertNotIn(("log.md", "date headings"), error_keys)

    def test_recursive_yaml_alias_reports_frontmatter_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n* [Minimal](minimal.md) - Small example.\n",
                encoding="utf-8",
            )
            (root / "log.md").write_text(
                "# Bundle Log\n\n## 2026-07-01\n\n* Created bundle.\n",
                encoding="utf-8",
            )
            (root / "minimal.md").write_text(
                "---\ntype: Minimal\nextension: &self\n  - *self\n---\n# Minimal\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            error_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "ERROR"
            }

            self.assertIn("frontmatter", error_subjects)

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

    def test_reference_style_index_links_count_as_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "See [Alpha][alpha-ref] and [Missing][missing-ref].\n\n"
                "[alpha-ref]:\n"
                "  concepts/alpha.md\n"
                '  "Alpha"\n'
                "[missing-ref]: concepts/missing.md\n"
                "[unused-ref]: concepts/unused.md\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            # alpha.md is only reachable via a reference-style link, so it
            # must not be reported as unindexed; the reference-style link
            # to a genuinely missing concept must still be reported.
            self.assertNotIn("index", warning_subjects)
            self.assertIn("link:concepts/missing.md", warning_subjects)
            # unused-ref is never referenced by any [text][unused-ref] or
            # shortcut [unused-ref] use, so CommonMark renders no link at
            # all for it; it must not be reported as a broken link either.
            self.assertNotIn("link:concepts/unused.md", warning_subjects)

    def test_protocol_relative_urls_are_external_to_link_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "host").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "See [colliding remote](//host/path.md).\n"
                "See [missing remote](&#47;&#47;missing/path.md).\n",
                encoding="utf-8",
            )
            (root / "host" / "path.md").write_text(
                "---\n"
                "type: Minimal\n"
                "title: Local path\n"
                "description: A local concept that shares the remote path.\n"
                "---\n"
                "# Local path\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_keys = {
                (finding.path, finding.subject)
                for finding in findings
                if finding.severity == "WARNING"
            }

            # The colliding network-path reference must not index the local
            # concept, and the unmatched one must not become a broken link.
            self.assertIn(("host/path.md", "index"), warning_keys)
            self.assertNotIn(
                ("index.md", "link:&#47;&#47;missing/path.md"),
                warning_keys,
            )

    def test_balanced_reference_link_text_counts_as_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "See [the [Alpha] concept][alpha-ref].\n\n"
                "[alpha-ref]: concepts/alpha.md\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("index", warning_subjects)
            self.assertFalse(
                any(subject.startswith("link:") for subject in warning_subjects)
            )

    def test_html_comment_link_does_not_count_as_indexed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "<!-- [Alpha](concepts/alpha.md) -->\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertIn("index", warning_subjects)

    def test_balanced_and_percent_encoded_links_share_adapter_resolution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "[Alpha](concepts/alpha%2Emd)\n\n"
                "[Missing](concepts/missing(v2).md)\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("index", warning_subjects)
            self.assertIn("link:concepts/missing(v2).md", warning_subjects)

    def test_percent_encoded_colon_link_is_validated_as_bundle_relative(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "[Missing](manual%3Av2.md)\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertIn("link:manual%3Av2.md", warning_subjects)

    def test_commonmark_escaped_and_entity_links_share_adapter_resolution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "[Alpha](concepts/alpha\\.md)\n\n"
                "[Beta](concepts/beta&#46;md)\n\n"
                "[Missing](concepts/missing\\.md)\n\n"
                "[External](https&#58;//example.com)\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )
            (root / "concepts" / "beta.md").write_text(
                "---\ntype: Minimal\n---\n# Beta\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("index", warning_subjects)
            self.assertIn("link:concepts/missing\\.md", warning_subjects)
            self.assertNotIn("link:https&#58;//example.com", warning_subjects)

    def test_commonmark_encoded_url_delimiters_share_adapter_resolution(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            (root / "concepts").mkdir(parents=True)
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "[Escaped fragment](concepts/alpha.md\\#details)\n\n"
                "[Entity fragment](concepts/alpha.md&#35;details)\n\n"
                "[Escaped query](concepts/alpha.md\\?view=full)\n\n"
                "[Entity query](concepts/alpha.md&#63;view=full)\n",
                encoding="utf-8",
            )
            (root / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\n# Alpha\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("index", warning_subjects)
            self.assertFalse(
                any(subject.startswith("link:") for subject in warning_subjects)
            )

    def test_nested_bracket_link_label_broken_target_is_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "[see [missing]](missing.md)\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertIn("link:missing.md", warning_subjects)

    def test_non_markdown_local_targets_are_not_concept_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "# Concepts\n\n"
                "![Diagram](diagram.png)\n\n"
                "[PDF](manual.pdf)\n\n"
                "[Script](references/check.py)\n\n"
                "[Missing concept](missing.md)\n\n"
                "[Missing section](guide/)\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("link:diagram.png", warning_subjects)
            self.assertNotIn("link:manual.pdf", warning_subjects)
            self.assertNotIn("link:references/check.py", warning_subjects)
            self.assertIn("link:missing.md", warning_subjects)
            self.assertIn("link:guide/", warning_subjects)

    def test_link_shaped_text_inside_code_block_is_not_flagged_as_broken(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n# Empty Index\n", encoding="utf-8"
            )
            (root / "example.md").write_text(
                "---\ntype: Minimal\n---\n"
                "```markdown\n"
                "[missing](missing.md)\n"
                "```\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("link:missing.md", warning_subjects)

    def test_link_shaped_text_inside_commonmark_code_spans_is_not_flagged(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n# Empty Index\n", encoding="utf-8"
            )
            (root / "example.md").write_text(
                "---\ntype: Minimal\n---\n"
                "Long delimiter: ``code ` [missing](missing.md)``.\n\n"
                "Multiline: ``code\n[also missing](also-missing.md)``.\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            warning_subjects = {
                finding.subject
                for finding in findings
                if finding.severity == "WARNING"
            }

            self.assertNotIn("link:missing.md", warning_subjects)
            self.assertNotIn("link:also-missing.md", warning_subjects)

    def test_root_index_without_declared_version_is_conformant(self) -> None:
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

            self.assertNotIn("okf_version", errors)

    def test_root_index_with_wrong_declared_version_still_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.1\"\n---\n# Bundle\n", encoding="utf-8"
            )
            (root / "log.md").write_text(
                "# Bundle Log\n\n## 2026-07-01\n\n* Created.\n", encoding="utf-8"
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            errors = {
                finding.subject for finding in findings if finding.severity == "ERROR"
            }

            self.assertIn("okf_version", errors)

    def test_duplicate_titles_preserve_distinct_non_ascii_titles(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "* [Architecture](architecture.md)\n"
                "* [Runbook](runbook.md)\n"
                "* [Copy](copy.md)\n",
                encoding="utf-8",
            )
            (root / "architecture.md").write_text(
                "---\ntype: Concept\ntitle: アーキテクチャ\n---\n# Architecture\n",
                encoding="utf-8",
            )
            (root / "runbook.md").write_text(
                "---\ntype: Concept\ntitle: 運用手順\n---\n# Runbook\n",
                encoding="utf-8",
            )
            (root / "copy.md").write_text(
                "---\ntype: Concept\ntitle: Architecture\n---\n# Copy\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            duplicate_findings = [
                finding for finding in findings if finding.subject == "title"
            ]

            self.assertEqual(duplicate_findings, [])

    def test_duplicate_titles_still_detected_for_ascii_variants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "okf"
            root.mkdir()
            (root / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Concepts\n\n"
                "* [A](a.md)\n"
                "* [B](b.md)\n",
                encoding="utf-8",
            )
            (root / "a.md").write_text(
                "---\ntype: Concept\ntitle: Architecture\n---\n# A\n",
                encoding="utf-8",
            )
            (root / "b.md").write_text(
                "---\ntype: Concept\ntitle: ARCHITECTURE\n---\n# B\n",
                encoding="utf-8",
            )

            findings = validate_bundle(root, today=date(2026, 7, 3))
            duplicate_subjects = {
                str(finding.path)
                for finding in findings
                if finding.subject == "title"
            }

            self.assertEqual(duplicate_subjects, {"a.md", "b.md"})


if __name__ == "__main__":
    unittest.main()
