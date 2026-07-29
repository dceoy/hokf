from __future__ import annotations

import filecmp
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path, PurePosixPath

import yaml

from tools.okf_common import FrontMatterError, split_front_matter
from tools.okf_hugo_adapter import (
    check_generated_content,
    collect_directory_differences,
    generate_content,
    iter_link_targets,
    main,
    rewrite_markdown_links,
)


SITE_DIR = Path(__file__).resolve().parents[1] / "site"


def load_generated(path: Path) -> tuple[dict[str, object], str]:
    metadata, body, present = split_front_matter(path.read_text(encoding="utf-8"))
    assert present and metadata is not None
    return metadata, body


def build_with_real_hugo(src: Path) -> Path:
    """Generate Hugo content from src and build it with the real hugo binary.

    Returns the public/ directory produced by the build.
    """
    root = Path(tempfile.mkdtemp())
    generate_content(src, root / "content")
    shutil.copytree(SITE_DIR / "layouts", root / "layouts")
    shutil.copytree(SITE_DIR / "assets", root / "assets")
    shutil.copy(SITE_DIR / "hugo.toml", root / "hugo.toml")
    public = root / "public"
    subprocess.run(
        ["hugo", "--source", str(root), "--destination", str(public)],
        check=True,
        capture_output=True,
        text=True,
    )
    return public


class OkfHugoAdapterTest(unittest.TestCase):
    def test_preserves_nested_metadata_unknown_fields_and_unknown_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\nokf_version: \"0.2\"\n---\n"
                "# Knowledge\n\n* [Alpha](concepts/alpha.md)\n",
                encoding="utf-8",
            )
            (src / "concepts" / "alpha.md").write_text(
                "---\n"
                "type: Producer Defined Type\n"
                "title: Alpha\n"
                "generated: {by: process:test, at: 2026-07-01T12:00:00Z}\n"
                "verified:\n"
                "  - {by: human:reviewer, at: 2026-07-02T12:00:00Z}\n"
                "sources:\n"
                "  - id: source\n"
                "    resource: https://example.com/source\n"
                "    signals: {confidence: high}\n"
                "status: stable\n"
                "stale_after: 2027-01-01\n"
                "producer_extension:\n"
                "  nested: [one, {two: 2}]\n"
                "  code: 0123\n"
                "okf_type: producer-owned-value\n"
                "---\n"
                "# Alpha\n\nSee [the root](/index.md).\n",
                encoding="utf-8",
            )

            self.assertEqual(generate_content(src, dst), 2)

            index_metadata, index_body = load_generated(dst / "_index.md")
            self.assertEqual(index_metadata["params"]["okf"]["okf_version"], "0.2")
            self.assertEqual(index_metadata["type"], "knowledge")
            self.assertIn("[Alpha](/concepts/alpha/)", index_body)

            metadata, body = load_generated(dst / "concepts" / "alpha.md")
            self.assertEqual(metadata["type"], "Producer Defined Type")
            okf_params = metadata["params"]["okf"]
            self.assertEqual(okf_params["okf_type"], "producer-owned-value")
            self.assertEqual(
                okf_params["producer_extension"],
                {"nested": ["one", {"two": 2}], "code": "0123"},
            )
            self.assertEqual(
                okf_params["sources"][0]["signals"],
                {"confidence": "high"},
            )
            self.assertIn("[the root](/)", body)

    def test_concept_with_only_type_and_legacy_timestamp_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "minimal.md").write_text(
                "---\ntype: Minimal\ntimestamp: 2026-01-01T00:00:00Z\n---\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            metadata, _ = load_generated(dst / "minimal.md")

            self.assertEqual(metadata["type"], "Minimal")
            self.assertIn("timestamp", metadata["params"]["okf"])
            self.assertNotIn("generated", metadata["params"]["okf"])

    def test_reserved_log_is_not_mapped_as_a_concept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "log.md").write_text(
                "# Log\n\n## 2026-07-01\n\n* Created the bundle.\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            metadata, _ = load_generated(dst / "log.md")

            self.assertEqual(metadata["type"], "knowledge")
            self.assertNotIn("okf_type", metadata)

    def test_check_reports_current_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            src.mkdir(parents=True)
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            generate_content(src, dst)

            self.assertEqual(check_generated_content(src, dst), 0)

    def test_deep_comparison_catches_content_differing_files_with_same_stat(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            expected = Path(tmp) / "expected"
            actual = Path(tmp) / "actual"
            expected.mkdir()
            actual.mkdir()
            (expected / "_index.md").write_bytes(b"---\ntype: knowledge\n---\nAAAA\n")
            (actual / "_index.md").write_bytes(b"---\ntype: knowledge\n---\nBBBB\n")

            # Equal size and an identical forced mtime give both files the
            # same os.stat() signature despite differing bytes, which is
            # exactly what a shallow (default) filecmp.dircmp misses.
            fixed_mtime = 1_700_000_000.0
            os.utime(expected / "_index.md", (fixed_mtime, fixed_mtime))
            os.utime(actual / "_index.md", (fixed_mtime, fixed_mtime))

            shallow_comparison = filecmp.dircmp(expected, actual)
            self.assertEqual(
                collect_directory_differences(shallow_comparison), []
            )

            deep_comparison = filecmp.dircmp(expected, actual, shallow=False)
            self.assertEqual(
                collect_directory_differences(deep_comparison),
                ["content differs: _index.md"],
            )

    def test_check_reports_file_directory_type_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            dst.mkdir()
            (src / "index.md").write_text("# Index\n", encoding="utf-8")
            (dst / "_index.md").mkdir()

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
                self.assertEqual(check_generated_content(src, dst), 1)

            self.assertIn(
                "type differs or cannot compare: _index.md",
                stderr.getvalue(),
            )

    def test_check_reports_missing_destination_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            src.mkdir(parents=True)
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as stderr:
                self.assertEqual(check_generated_content(src, dst), 1)

            self.assertIn(str(dst), stderr.getvalue())
            self.assertFalse(dst.exists())

    def test_cli_check_reports_missing_destination_before_generation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            src.mkdir(parents=True)
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(
                    main(["--src", str(src), "--dst", str(dst), "--check"]), 1
                )

            self.assertFalse(dst.exists())

    def test_cli_reports_invalid_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "bad.md").write_text("---\ntype: [\n---\n", encoding="utf-8")

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(
                    main(["--src", str(src), "--dst", str(root / "content")]),
                    1,
                )

    def test_cli_reports_recursive_yaml_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "cyclic.md").write_text(
                "---\ntype: Minimal\nextension: &self\n  - *self\n---\n",
                encoding="utf-8",
            )

            stderr = StringIO()
            with redirect_stdout(StringIO()), redirect_stderr(stderr):
                self.assertEqual(
                    main(["--src", str(src), "--dst", str(root / "content")]),
                    1,
                )

            self.assertIn("recursive", stderr.getvalue())

    def test_cli_rejects_symbolic_link_document(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            outside = root / "outside.md"
            outside.write_text("---\ntype: Secret\n---\n", encoding="utf-8")
            (src / "linked.md").symlink_to(outside)

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(
                    main(["--src", str(src), "--dst", str(root / "content")]),
                    1,
                )

    def test_cli_rejects_overlapping_source_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()

            with self.assertRaises(SystemExit):
                main(["--src", str(src), "--dst", str(root), "--clean"])

    def test_front_matter_preserves_legacy_yaml_scalars(self) -> None:
        metadata, _, present = split_front_matter(
            "---\n"
            "on: keep\n"
            "answer: yes\n"
            "flag: true\n"
            "code: 0123\n"
            "negative_code: -0123\n"
            "ratio: 12:34\n"
            "---\nBody\n"
        )

        self.assertTrue(present)
        self.assertEqual(metadata["on"], "keep")
        self.assertEqual(metadata["answer"], "yes")
        self.assertIs(metadata["flag"], True)
        # YAML 1.2 core-schema decimal integers are either zero or begin with
        # a non-zero digit, so a multi-digit leading-zero value stays a string.
        self.assertEqual(metadata["code"], "0123")
        self.assertEqual(metadata["negative_code"], "-0123")
        # A colon-separated digit string does not match any core-schema
        # scalar tag, so it is preserved as a string, not the YAML-1.1
        # sexagesimal reinterpretation (754).
        self.assertEqual(metadata["ratio"], "12:34")

    def test_front_matter_rejects_duplicate_mapping_keys_at_every_depth(
        self,
    ) -> None:
        examples = {
            "top-level": (
                "---\n"
                "type: First\n"
                "type: Second\n"
                "---\n"
            ),
            "nested": (
                "---\n"
                "type: Minimal\n"
                "extension:\n"
                "  value: first\n"
                "  value: second\n"
                "---\n"
            ),
        }

        for name, markdown in examples.items():
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    FrontMatterError, "found duplicate key"
                ):
                    split_front_matter(markdown)

    def test_front_matter_rejects_recursive_yaml_alias(self) -> None:
        markdown = (
            "---\n"
            "type: Minimal\n"
            "extension: &self\n"
            "  - *self\n"
            "---\n"
            "Body\n"
        )

        with self.assertRaisesRegex(FrontMatterError, "recursive"):
            split_front_matter(markdown)

    def test_front_matter_ignores_indented_delimiter_in_block_scalar(self) -> None:
        metadata, body, present = split_front_matter(
            "---\n"
            "type: Minimal\n"
            "note: |\n"
            "  before\n"
            "  ---\n"
            "  after\n"
            "---\n"
            "Body\n"
        )

        self.assertTrue(present)
        self.assertEqual(metadata["note"], "before\n---\nafter\n")
        self.assertEqual(body, "Body\n")

    def test_front_matter_requires_unindented_opening_delimiter(self) -> None:
        markdown = "    ---\ntype: Minimal\n---\nBody\n"

        metadata, body, present = split_front_matter(markdown)

        self.assertFalse(present)
        self.assertIsNone(metadata)
        self.assertEqual(body, markdown)

    def test_standalone_underscore_index_builds_as_leaf_page_not_section(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "_index.md").write_text(
                "---\ntype: Minimal\n---\n# Underscore Concept\n", encoding="utf-8"
            )

            # A root index.md and a standalone _index.md concept no longer
            # collide: index.md still maps to the root _index.md file, while
            # _index.md is routed into a same-named directory so Hugo does
            # not treat it as the section's own branch-bundle page.
            self.assertEqual(generate_content(src, dst), 2)
            self.assertTrue((dst / "_index.md").is_file())
            self.assertTrue((dst / "_index" / "index.md").is_file())
            _, root_body = load_generated(dst / "_index.md")
            self.assertIn("# Root", root_body)
            _, concept_body = load_generated(dst / "_index" / "index.md")
            self.assertIn("# Underscore Concept", concept_body)

    def test_standalone_nested_underscore_index_builds_as_leaf_page(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "concepts" / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Concepts\n", encoding="utf-8"
            )
            (src / "concepts" / "_index.md").write_text(
                "---\ntype: Minimal\n---\n# Nested Underscore Concept\n",
                encoding="utf-8",
            )

            self.assertEqual(generate_content(src, dst), 2)
            self.assertTrue((dst / "concepts" / "_index.md").is_file())
            self.assertTrue(
                (dst / "concepts" / "_index" / "index.md").is_file()
            )
            _, concept_body = load_generated(
                dst / "concepts" / "_index" / "index.md"
            )
            self.assertIn("# Nested Underscore Concept", concept_body)

    def test_output_collision_between_underscore_index_and_its_own_directory(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "_index").mkdir(parents=True)
            (src / "_index.md").write_text(
                "---\ntype: Minimal\n---\n", encoding="utf-8"
            )
            (src / "_index" / "index.md").write_text(
                "---\ntype: Minimal\n---\n", encoding="utf-8"
            )

            # Both a standalone `_index.md` and a `_index/index.md` concept
            # map to the same generated path and public permalink
            # (`/_index/`); the collision guard must still reject this pair.
            with self.assertRaises(FrontMatterError):
                generate_content(src, dst)

    def test_output_collision_between_leaf_and_nested_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "foo").mkdir(parents=True)
            (src / "foo.md").write_text("---\ntype: Minimal\n---\n", encoding="utf-8")
            (src / "foo" / "index.md").write_text(
                "---\ntype: Minimal\n---\n", encoding="utf-8"
            )

            with self.assertRaises(FrontMatterError):
                generate_content(src, dst)

    def test_cli_rejects_symlinked_parent_when_target_already_has_content(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            target = root / "target"
            (target / "content").mkdir(parents=True)
            (target / "content" / "keep.txt").write_text("keep", encoding="utf-8")

            site_link = root / "site"
            site_link.symlink_to(target, target_is_directory=True)
            dst = site_link / "content"

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--src", str(src), "--dst", str(dst), "--clean"])

            self.assertTrue(site_link.is_symlink())
            self.assertTrue((target / "content" / "keep.txt").exists())

    def test_cli_rejects_symlinked_destination_before_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            target = root / "target"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")

            dst = root / "site" / "content"
            dst.parent.mkdir(parents=True)
            dst.symlink_to(target, target_is_directory=True)

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--src", str(src), "--dst", str(dst), "--clean"])

            self.assertTrue(dst.is_symlink())
            self.assertTrue((target / "keep.txt").exists())

    def test_cli_rejects_symlinked_destination_parent_before_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            target = root / "target"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")

            site_link = root / "site"
            site_link.symlink_to(target, target_is_directory=True)
            dst = site_link / "content"

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--src", str(src), "--dst", str(dst), "--clean"])

            self.assertTrue(site_link.is_symlink())
            self.assertTrue((target / "keep.txt").exists())

    def test_cli_rejects_symlink_hidden_by_nonexistent_dot_dot_segment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            target = root / "target"
            target.mkdir()
            (target / "keep.txt").write_text("keep", encoding="utf-8")

            link = root / "link"
            link.symlink_to(target, target_is_directory=True)

            # "missing" does not exist, so a naive lexical walk of each raw
            # component never sees a symlink, yet dst.resolve() collapses
            # "missing/.." and follows the symlink at "link".
            dst = root / "missing" / ".." / "link"

            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit):
                    main(["--src", str(src), "--dst", str(dst), "--clean"])

            self.assertTrue(link.is_symlink())
            self.assertTrue((target / "keep.txt").exists())

    def test_rewrite_markdown_links_skips_code_regions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text("---\ntype: Minimal\n---\nChild\n", encoding="utf-8")
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "Inline example: `[child](child.md)`.\n\n"
                "```markdown\n"
                "<!-- [child](child.md)\n"
                "<div>\n"
                "```\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("`[child](child.md)`", body)
            self.assertIn(
                "```markdown\n<!-- [child](child.md)\n<div>\n```", body
            )
            self.assertIn("Real link: [child](/child/)", body)

    def test_rewrite_markdown_links_skips_raw_html_and_comments(self) -> None:
        body = (
            "<!-- [commented](child.md) -->\n\n"
            '<span data-example="[attribute](child.md)">HTML</span>\n\n'
            '<div data-example="[block-attribute](child.md)">\n'
            "[block-content](child.md)\n"
            "</div>\n\n"
            "Real link: [child](child.md)\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn("<!-- [commented](child.md) -->", rewritten)
        self.assertIn('data-example="[attribute](child.md)"', rewritten)
        self.assertIn("[block-content](child.md)", rewritten)
        self.assertIn("Real link: [child](/concepts/child/)", rewritten)
        self.assertEqual(iter_link_targets(body), ["child.md"])

    def test_rewrite_markdown_links_handles_reference_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref] and [the root][root-ref].\n\n"
                "[child-ref]:\n"
                "  child.md\n"
                '  "Child"\n'
                "[root-ref]:\n"
                "  <../index.md>\n"
                '  "Root"\n',
                encoding="utf-8",
            )
            (src / "index.md").write_text("---\ntype: Minimal\n---\n# Root\n", encoding="utf-8")

            generate_content(src, dst)
            _, body = load_generated(dst / "concepts" / "parent.md")

            self.assertIn('[child-ref]:\n  /concepts/child/\n  "Child"', body)
            self.assertIn('[root-ref]:\n  </>\n  "Root"', body)

    def test_reference_definition_title_is_not_rewritten_as_inline_link(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "other.md").write_text(
                "---\ntype: Minimal\n---\nOther\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                '[ref]: child.md "[fake](other.md)"\n',
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn('[ref]: /child/ "[fake](other.md)"', body)
            self.assertEqual(
                iter_link_targets('[ref]: child.md "[fake](other.md)"\n'),
                ["child.md"],
            )

    def test_crlf_fence_does_not_hide_following_links_or_reference_definitions(
        self,
    ) -> None:
        body = (
            "# Parent\r\n\r\n"
            "```markdown\r\n"
            "[example](child.md)\r\n"
            "```\r\n\r\n"
            "Real link: [child](child.md)\r\n\r\n"
            "[child-ref]: child.md\r\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn("[example](child.md)", rewritten)
        self.assertIn("Real link: [child](/concepts/child/)", rewritten)
        self.assertIn("[child-ref]: /concepts/child/\r\n", rewritten)

    def test_rewrite_markdown_links_handles_all_inline_destination_and_title_forms(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "Angle-bracketed: [child](<child.md>).\n\n"
                "Single-quoted title: [child](child.md 'Child').\n\n"
                "Parenthesized title: [child](child.md (Child)).\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("Angle-bracketed: [child](</child/>).", body)
            self.assertIn("Single-quoted title: [child](/child/ 'Child').", body)
            self.assertIn("Parenthesized title: [child](/child/ (Child)).", body)

    def test_rewrite_markdown_links_handles_balanced_parentheses_in_destination(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "concepts" / "child(v2).md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "Balanced: [child](child(v2).md).\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "concepts" / "parent.md")

            self.assertIn("Balanced: [child](/concepts/child%28v2%29/).", body)

    def test_rewrite_markdown_links_preserves_multiline_link_formatting(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "[child](\n"
                "  child.md\n"
                '  "A multiline\n'
                '  title"\n'
                ")\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn(
                "[child](\n  /child/\n  \"A multiline\n  title\"\n)",
                body,
            )

    def test_rewrite_markdown_links_decodes_commonmark_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "Escaped: [child](child\\.md?view=full#details).\n\n"
                "Entity: [child](child&#46;md).\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn(
                "Escaped: [child](/child/?view=full#details).",
                body,
            )
            self.assertIn("Entity: [child](/child/).", body)

    def test_rewrite_markdown_links_skips_longer_and_multiline_code_spans(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "Long delimiter: ``code ` [child](child.md)``.\n\n"
                "Multiline: ``code\n[child](child.md)``.\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("``code ` [child](child.md)``", body)
            self.assertIn("``code\n[child](child.md)``", body)
            self.assertIn("Real link: [child](/child/)", body)

    def test_unmatched_backticks_do_not_form_code_span_across_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "An unmatched ` opener.\n\n"
                "Real link: [child](child.md) and another ` delimiter.\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("Real link: [child](/child/)", body)

    def test_rewrite_markdown_links_skips_indented_fence_and_longer_closing_fence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "   ```markdown\n"
                "   [child](child.md)\n"
                "   ```\n\n"
                "````markdown\n"
                "[child](child.md)\n"
                "`````\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("   ```markdown\n   [child](child.md)\n   ```", body)
            self.assertIn("````markdown\n[child](child.md)\n`````", body)
            self.assertIn("Real link: [child](/child/)", body)

    def test_rewrite_markdown_links_skips_fences_in_block_containers(self) -> None:
        body = (
            "> ````markdown\n"
            "> [quoted](child.md)\n"
            "> `````\n\n"
            "- ````markdown\n"
            "  [listed](child.md)\n"
            "  `````\n\n"
            "Real link: [child](child.md)\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn("> [quoted](child.md)", rewritten)
        self.assertIn("  [listed](child.md)", rewritten)
        self.assertIn("Real link: [child](/concepts/child/)", rewritten)
        self.assertEqual(iter_link_targets(body), ["child.md"])

    def test_rewrite_markdown_links_handles_complete_reference_labels(
        self,
    ) -> None:
        body = (
            "See [escaped][foo \\]] and [multiline][foo bar].\n\n"
            "[foo \\]]: child.md\n"
            "[foo\n bar]: child.md\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn("[foo \\]]: /concepts/child/", rewritten)
        self.assertIn("[foo\n bar]: /concepts/child/", rewritten)
        self.assertEqual(iter_link_targets(body), ["child.md", "child.md"])

    def test_rewrite_markdown_links_allows_line_endings_in_inline_labels(
        self,
    ) -> None:
        body = "See [the\nchild](child.md).\n"

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertEqual(rewritten, "See [the\nchild](/concepts/child/).\n")
        self.assertEqual(iter_link_targets(body), ["child.md"])

    def test_rewrite_markdown_links_handles_separate_links_across_paragraphs(
        self,
    ) -> None:
        body = "See [child](child.md).\n\nAnother [child](child.md).\n"

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertEqual(
            rewritten,
            "See [child](/concepts/child/).\n\n"
            "Another [child](/concepts/child/).\n",
        )
        self.assertEqual(iter_link_targets(body), ["child.md", "child.md"])

    def test_inline_link_label_does_not_span_a_blank_line(self) -> None:
        body = "[not a label\n\n](child.md)\n"

        self.assertEqual(iter_link_targets(body), [])

    def test_rewrite_markdown_links_skips_indented_code_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "child.md").write_text(
                "---\ntype: Minimal\n---\nChild\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "    [child](child.md)\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("    [child](child.md)", body)
            self.assertIn("Real link: [child](/child/)", body)

    def test_rewrite_markdown_links_skips_indented_code_in_block_containers(
        self,
    ) -> None:
        body = (
            ">     [quoted](child.md)\n\n"
            "- >     [nested](child.md)\n\n"
            "Real link: [child](child.md)\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn(">     [quoted](child.md)", rewritten)
        self.assertIn("- >     [nested](child.md)", rewritten)
        self.assertIn("Real link: [child](/concepts/child/)", rewritten)
        self.assertEqual(iter_link_targets(body), ["child.md"])

    def test_rewrite_markdown_links_skips_indented_code_in_list_item(
        self,
    ) -> None:
        body = (
            "- Example\n\n"
            "      [example](child.md)\n\n"
            "Real link: [child](child.md)\n"
        )

        rewritten = rewrite_markdown_links(
            PurePosixPath("concepts/parent.md"),
            body,
            {
                PurePosixPath("concepts/parent.md"),
                PurePosixPath("concepts/child.md"),
            },
        )

        self.assertIn("      [example](child.md)", rewritten)
        self.assertIn("Real link: [child](/concepts/child/)", rewritten)
        self.assertEqual(iter_link_targets(body), ["child.md"])

    def test_rewrite_markdown_links_treats_indented_list_continuation_as_prose(
        self,
    ) -> None:
        # An indented continuation line of a loose list item is not a code
        # block per CommonMark, even though it is indented by 4+ spaces and
        # separated from the list marker by a blank line.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\nAlpha\n", encoding="utf-8"
            )
            (src / "concepts" / "beta.md").write_text(
                "---\ntype: Minimal\n---\nBeta\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "* [Alpha](concepts/alpha.md)\n\n"
                "    * [Beta](concepts/beta.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("* [Alpha](/concepts/alpha/)", body)
            self.assertIn("* [Beta](/concepts/beta/)", body)

    def test_rewrite_markdown_links_treats_indented_prose_continuation_as_prose(
        self,
    ) -> None:
        # A nested list item whose own continuation is an indented prose
        # paragraph, followed by a further indented sub-item, must not be
        # mistaken for an indented code block either: the line right before
        # the blank-line gap is itself indented continuation, not a bare
        # list marker.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "concepts" / "alpha.md").write_text(
                "---\ntype: Minimal\n---\nAlpha\n", encoding="utf-8"
            )
            (src / "concepts" / "beta.md").write_text(
                "---\ntype: Minimal\n---\nBeta\n", encoding="utf-8"
            )
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Index\n\n"
                "* [Alpha](concepts/alpha.md)\n\n"
                "    Some prose continuation.\n\n"
                "    * [Beta](concepts/beta.md)\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            _, body = load_generated(dst / "_index.md")

            self.assertIn("* [Alpha](/concepts/alpha/)", body)
            self.assertIn("* [Beta](/concepts/beta/)", body)

    def test_producer_metadata_is_namespaced_under_params_okf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "content"
            src.mkdir()
            (src / "alpha.md").write_text(
                "---\n"
                "type: Minimal\n"
                "title: Alpha\n"
                "description: An example concept.\n"
                "draft: true\n"
                "url: /somewhere-else/\n"
                "resource: https://example.com\n"
                "---\n"
                "# Alpha\n",
                encoding="utf-8",
            )

            generate_content(src, dst)
            metadata, _ = load_generated(dst / "alpha.md")

            self.assertEqual(metadata["title"], "Alpha")
            self.assertEqual(metadata["description"], "An example concept.")
            self.assertNotIn("draft", metadata)
            self.assertNotIn("url", metadata)
            self.assertNotIn("resource", metadata)
            okf_params = metadata["params"]["okf"]
            self.assertIs(okf_params["draft"], True)
            self.assertEqual(okf_params["url"], "/somewhere-else/")
            self.assertEqual(okf_params["resource"], "https://example.com")


@unittest.skipUnless(shutil.which("hugo"), "hugo binary is not installed")
class OkfHugoAdapterRealBuildTest(unittest.TestCase):
    def test_indented_code_in_list_item_is_preserved_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "- Example\n\n"
                "      [example](child.md)\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("<code>[example](child.md)", parent_html)
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_block_container_fences_preserve_code_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "> ````markdown\n"
                "> [quoted](child.md)\n"
                "> `````\n\n"
                "- ````markdown\n"
                "  [listed](child.md)\n"
                "  `````\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            highlighted_target = 'style="color:#a6e22e">child.md</span>'
            self.assertEqual(parent_html.count(highlighted_target), 2)
            self.assertIn('href="/concepts/child/">child</a>', parent_html)

    def test_type_seven_html_does_not_interrupt_quoted_paragraph_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "> Parent text\n"
                "> <span>\n"
                "> [child](child.md)\n"
                "> </span>\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_html_block_indented_in_list_item_is_preserved_in_real_hugo_build(
        self,
    ) -> None:
        # Renders with `unsafe = true` (unlike build_with_real_hugo's default
        # config) so the raw-HTML block's content is visible in the output
        # instead of collapsing to "<!-- raw HTML omitted -->" either way,
        # which would hide whether the internal link was wrongly rewritten.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "10. Example\n\n"
                "    <div>\n"
                "    [example](child.md)\n"
                "    </div>\n\n"
                "Real link: [child](child.md)\n",
                encoding="utf-8",
            )

            generate_content(src, root / "content")
            shutil.copytree(SITE_DIR / "layouts", root / "layouts")
            shutil.copytree(SITE_DIR / "assets", root / "assets")
            hugo_config = (SITE_DIR / "hugo.toml").read_text(encoding="utf-8")
            (root / "hugo.toml").write_text(
                hugo_config.replace("unsafe = false", "unsafe = true"),
                encoding="utf-8",
            )
            self.assertIn("unsafe = true", (root / "hugo.toml").read_text())
            public = root / "public"
            subprocess.run(
                ["hugo", "--source", str(root), "--destination", str(public)],
                check=True,
                capture_output=True,
                text=True,
            )

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("[example](child.md)", parent_html)
            self.assertNotIn('href="/concepts/child/">example</a>', parent_html)
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_label_forms_resolve_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [escaped][foo \\]] and [multiline][foo bar].\n\n"
                "[foo \\]]: child.md\n"
                "[foo\n bar]: child.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">escaped</a>', parent_html)
            self.assertIn('href="/concepts/child/">multiline</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_multiline_inline_label_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the\nchild](child.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the\nchild</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_multiline_reference_definition_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref].\n\n"
                "[child-ref]:\n"
                "  child.md\n"
                '  "Child title"\n',
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/child/" title="Child title">the child</a>',
                parent_html,
            )
            self.assertNotIn('href="child.md"', parent_html)

    def test_balanced_parenthesis_link_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child(v2).md").write_text(
                "---\ntype: Minimal\n---\n# Child v2\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child](child(v2).md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/child%28v2%29/">the child</a>', parent_html
            )
            self.assertNotIn('href="child(v2).md"', parent_html)

    def test_percent_encoded_markdown_path_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child](child%2Emd?view=full#details).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/child/?view=full#details">the child</a>',
                parent_html,
            )
            self.assertNotIn("child%2Emd", parent_html)

    def test_multiline_inline_link_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "[the child](\n"
                "  child.md\n"
                '  "Child title"\n'
                ")\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/child/" title="Child title">the child</a>',
                parent_html,
            )
            self.assertNotIn('href="child.md"', parent_html)

    def test_escaped_markdown_path_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child](child\\.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_commonmark_encoded_url_delimiters_resolve_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "[escaped fragment](child.md\\#details)\n\n"
                "[entity fragment](child.md&#35;details)\n\n"
                "[escaped query](child.md\\?view=full)\n\n"
                "[entity query](child.md&#63;view=full)\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/child/#details">escaped fragment</a>',
                parent_html,
            )
            self.assertIn(
                'href="/concepts/child/#details">entity fragment</a>',
                parent_html,
            )
            self.assertIn(
                'href="/concepts/child/?view=full">escaped query</a>',
                parent_html,
            )
            self.assertIn(
                'href="/concepts/child/?view=full">entity query</a>',
                parent_html,
            )
            self.assertNotIn('href="child.md', parent_html)

    def test_space_in_resolved_path_is_percent_encoded_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "foo bar.md").write_text(
                "---\ntype: Minimal\n---\n# Foo Bar\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [foo bar](foo%20bar.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/foo%20bar/">foo bar</a>', parent_html
            )
            self.assertNotIn('href="/concepts/foo bar/"', parent_html)

    def test_hash_in_resolved_path_is_percent_encoded_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "foo#bar.md").write_text(
                "---\ntype: Minimal\n---\n# Foo Hash Bar\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [foo hash bar](foo%23bar.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn(
                'href="/concepts/foo%23bar/">foo hash bar</a>', parent_html
            )
            self.assertNotIn('href="/concepts/foo#bar/"', parent_html)

    def test_nested_bracket_link_label_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [see [child]](child.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">see [child]</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_innermost_nested_link_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "other.md").write_text(
                "---\ntype: Minimal\n---\n# Other\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [outer [inner](child.md)](other.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            # CommonMark disallows links within links: the innermost
            # `[inner](child.md)` is the only real link, and the outer
            # `[outer ...](other.md)` remains literal text around it.
            # (Goldmark inserts a soft line break before the trailing `]` in
            # this construct; the assertions below tolerate that.)
            self.assertIn('[outer <a href="/concepts/child/">inner</a>', parent_html)
            self.assertIn("](other.md)", parent_html)
            self.assertNotIn('href="other.md"', parent_html)
            self.assertNotIn('href="/concepts/other/"', parent_html)

    def test_image_nested_in_link_label_resolves_outer_link_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "target.md").write_text(
                "---\ntype: Minimal\n---\n# Target\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "[![badge](badge.svg)](target.md)\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            # An image does not disable an enclosing link in CommonMark, so
            # the outer link must still resolve and the image destination is
            # left untouched (it does not point at an OKF document).
            self.assertIn(
                '<a href="/concepts/target/"><img src="badge.svg" alt="badge">'
                "</a>",
                parent_html,
            )

    def test_reference_definition_inside_block_quote_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref].\n\n"
                "> [child-ref]: child.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_inside_list_item_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref].\n\n"
                "- [child-ref]: child.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_multiline_reference_definition_inside_block_quote_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref].\n\n"
                "> [child-ref]:\n"
                "> child.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_interrupting_paragraph_stays_literal_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [broken][orphan-ref].\n"
                "[orphan-ref]: child.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('href="/concepts/child/"', parent_html)
            self.assertIn("[orphan-ref]: child.md", parent_html)

    def test_reference_definition_after_heading_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n"
                "[child-ref]: child.md\n\n"
                "See [the child][child-ref].\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_consecutive_reference_definitions_resolve_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "sibling.md").write_text(
                "---\ntype: Minimal\n---\n# Sibling\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [a][a-ref] and [b][b-ref].\n\n"
                "[a-ref]:\n"
                "  child.md\n"
                "[b-ref]: sibling.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">a</a>', parent_html)
            self.assertIn('href="/concepts/sibling/">b</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)
            self.assertNotIn('href="sibling.md"', parent_html)

    def test_reference_definition_after_fenced_code_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "```text\n"
                "example\n"
                "```\n"
                "[ref]: child.md\n"
                "See [child][ref].\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_after_setext_heading_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "Parent\n"
                "===\n"
                "[ref]: child.md\n"
                "See [child][ref].\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_after_indented_code_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "    example code\n"
                "[ref]: child.md\n"
                "See [child][ref].\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_after_html_block_resolves_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "<!-- comment -->\n"
                "[ref]: child.md\n"
                "See [child][ref].\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_reference_definition_after_rejected_candidate_stays_literal_in_real_hugo_build(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "sibling.md").write_text(
                "---\ntype: Minimal\n---\n# Sibling\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [b][b].\n"
                "[a]: child.md\n"
                "[b]: sibling.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            # Neither candidate may interrupt the open paragraph started by
            # "See [b][b].", including the second ("b") candidate: the first
            # ("a") candidate is rejected too, so it must not reset the
            # paragraph state that "b" is then checked against.
            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertNotIn('href="/concepts/child/"', parent_html)
            self.assertNotIn('href="/concepts/sibling/"', parent_html)
            self.assertIn("[a]: child.md", parent_html)
            self.assertIn("[b]: sibling.md", parent_html)

    def test_escaped_bracket_link_label_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [child \\]](child.md).\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">child ]</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)

    def test_standalone_root_underscore_index_builds_distinct_from_home(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            src.mkdir()
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "_index.md").write_text(
                "---\ntype: Minimal\n---\n# Underscore Concept\n\n"
                "Standalone concept body.\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            self.assertTrue((public / "index.html").exists())
            self.assertIn("Root", (public / "index.html").read_text(encoding="utf-8"))
            self.assertTrue((public / "_index" / "index.html").exists())
            self.assertIn(
                "Standalone concept body",
                (public / "_index" / "index.html").read_text(encoding="utf-8"),
            )

    def test_standalone_nested_underscore_index_builds_distinct_from_section(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Concepts\n", encoding="utf-8"
            )
            (src / "concepts" / "_index.md").write_text(
                "---\ntype: Minimal\n---\n# Nested Underscore Concept\n\n"
                "Nested concept body.\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            self.assertTrue((public / "concepts" / "index.html").exists())
            self.assertIn(
                "Concepts",
                (public / "concepts" / "index.html").read_text(encoding="utf-8"),
            )
            self.assertTrue((public / "concepts" / "_index" / "index.html").exists())
            self.assertIn(
                "Nested concept body",
                (public / "concepts" / "_index" / "index.html").read_text(
                    encoding="utf-8"
                ),
            )

    def test_reference_style_links_resolve_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "child.md").write_text(
                "---\ntype: Minimal\n---\n# Child\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the child][child-ref] and [the root][root-ref].\n\n"
                "[child-ref]: child.md\n"
                "[root-ref]: /index.md\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('href="/concepts/child/">the child</a>', parent_html)
            self.assertIn('>the root</a>', parent_html)
            self.assertNotIn('href="child.md"', parent_html)
            self.assertNotIn('href="/index.md"', parent_html)
            self.assertNotIn('href="../index.md"', parent_html)

    def test_bracketed_reference_target_resolves_in_real_hugo_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "parent.md").write_text(
                "---\ntype: Minimal\n---\n"
                "# Parent\n\n"
                "See [the root][root-ref].\n\n"
                "[root-ref]: <../index.md> \"Root\"\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            parent_html = (public / "concepts" / "parent" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn('>the root</a>', parent_html)
            self.assertNotIn('href="../index.md"', parent_html)

    def test_leaf_page_and_sibling_directory_both_publish_in_real_hugo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            content = root / "content"
            (content / "foo").mkdir(parents=True)
            (content / "foo.md").write_text(
                "---\ntype: knowledge\n---\nFoo page body.\n", encoding="utf-8"
            )
            (content / "foo" / "bar.md").write_text(
                "---\ntype: knowledge\n---\nBar page body.\n", encoding="utf-8"
            )
            shutil.copytree(SITE_DIR / "layouts", root / "layouts")
            shutil.copytree(SITE_DIR / "assets", root / "assets")
            shutil.copy(SITE_DIR / "hugo.toml", root / "hugo.toml")
            public = root / "public"
            subprocess.run(
                ["hugo", "--source", str(root), "--destination", str(public)],
                check=True,
                capture_output=True,
                text=True,
            )

            # Regression test for a claimed foo.md/foo/bar.md URL collision:
            # the pinned Hugo binary adopts foo.md as the foo/ section page
            # rather than shadowing it, so both pages publish and are
            # reachable at their own URLs.
            foo_html = (public / "foo" / "index.html").read_text(encoding="utf-8")
            bar_html = (public / "foo" / "bar" / "index.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Foo page body.", foo_html)
            self.assertIn("Bar page body.", bar_html)

    def test_producer_keys_shaped_like_hugo_fields_do_not_affect_publishing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "okf"
            (src / "concepts").mkdir(parents=True)
            (src / "index.md").write_text(
                "---\ntype: Minimal\n---\n# Root\n", encoding="utf-8"
            )
            (src / "concepts" / "alpha.md").write_text(
                "---\n"
                "type: Minimal\n"
                "draft: true\n"
                "url: /somewhere-else/\n"
                "build:\n"
                "  render: never\n"
                "headless: true\n"
                "---\n"
                "# Alpha\n\nAlpha body.\n",
                encoding="utf-8",
            )

            public = build_with_real_hugo(src)

            # A conformant concept carrying producer keys that happen to
            # share names with Hugo-reserved front matter (draft, url,
            # build, headless) must still publish at its normal permalink:
            # none of those keys should reach Hugo's top-level front matter.
            alpha_html = public / "concepts" / "alpha" / "index.html"
            self.assertTrue(alpha_html.exists())
            self.assertIn("Alpha body.", alpha_html.read_text(encoding="utf-8"))
            self.assertFalse((public / "somewhere-else").exists())


if __name__ == "__main__":
    unittest.main()
