from __future__ import annotations

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

import yaml

from tools.okf_common import split_front_matter
from tools.okf_hugo_adapter import check_generated_content, generate_content, main


def load_generated(path: Path) -> tuple[dict[str, object], str]:
    metadata, body, present = split_front_matter(path.read_text(encoding="utf-8"))
    assert present and metadata is not None
    return metadata, body


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
                "okf_type: producer-owned-value\n"
                "---\n"
                "# Alpha\n\nSee [the root](/index.md).\n",
                encoding="utf-8",
            )

            self.assertEqual(generate_content(src, dst), 2)

            index_metadata, index_body = load_generated(dst / "_index.md")
            self.assertEqual(index_metadata["okf_version"], "0.2")
            self.assertEqual(index_metadata["type"], "knowledge")
            self.assertIn("[Alpha](/concepts/alpha/)", index_body)

            metadata, body = load_generated(dst / "concepts" / "alpha.md")
            self.assertEqual(metadata["type"], "Producer Defined Type")
            self.assertEqual(metadata["okf_type"], "producer-owned-value")
            self.assertEqual(
                metadata["producer_extension"],
                {"nested": ["one", {"two": 2}]},
            )
            self.assertEqual(
                metadata["sources"][0]["signals"],  # type: ignore[index]
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
            self.assertIn("timestamp", metadata)
            self.assertNotIn("generated", metadata)

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
            "---\non: keep\nanswer: yes\nflag: true\n---\nBody\n"
        )

        self.assertTrue(present)
        self.assertEqual(metadata["on"], "keep")
        self.assertEqual(metadata["answer"], "yes")
        self.assertIs(metadata["flag"], True)


if __name__ == "__main__":
    unittest.main()
