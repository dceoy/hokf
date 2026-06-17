from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.okf_hugo_adapter import check_generated_content, generate_content, main


class OkfHugoAdapterTest(unittest.TestCase):
    def test_generates_hugo_content_with_front_matter_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            (src / "concepts").mkdir(parents=True)
            (src / "logs").mkdir()
            (src / "index.md").write_text(
                "# Knowledge\n\nSee [Alpha](concepts/alpha.md).\n",
                encoding="utf-8",
            )
            (src / "concepts" / "alpha.md").write_text(
                "\n".join(
                    [
                        "---",
                        "title: Alpha",
                        "type: concept",
                        "description: First concept",
                        "tags:",
                        "  - example",
                        "---",
                        "# Alpha",
                        "",
                        "See [the log](../logs/entry.md#notes).",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (src / "logs" / "entry.md").write_text(
                "---\n"
                "title: Entry\n"
                "type: log\n"
                "timestamp: 2026-01-01\n"
                "---\n"
                "# Entry\n",
                encoding="utf-8",
            )

            self.assertEqual(generate_content(src, dst), 3)

            self.assertEqual(
                (dst / "_index.md").read_text(encoding="utf-8"),
                "---\n"
                "type: knowledge\n"
                "---\n"
                "\n"
                "# Knowledge\n"
                "\n"
                "See [Alpha](/concepts/alpha/).\n",
            )
            self.assertEqual(
                (dst / "concepts" / "alpha.md").read_text(encoding="utf-8"),
                "---\n"
                "type: knowledge\n"
                "title: Alpha\n"
                "description: First concept\n"
                "tags:\n"
                "  - example\n"
                "params:\n"
                "  okf_type: concept\n"
                "---\n"
                "\n"
                "# Alpha\n"
                "\n"
                "See [the log](/logs/entry/#notes).\n",
            )

    def test_check_reports_current_generated_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            dst = root / "site" / "content"
            src.mkdir(parents=True)
            (src / "index.md").write_text("# Index\n", encoding="utf-8")

            generate_content(src, dst)
            (dst / ".gitkeep").write_text("", encoding="utf-8")

            self.assertEqual(check_generated_content(src, dst), 0)

    def test_cli_rejects_overlapping_source_and_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "okf"
            src.mkdir()

            with self.assertRaises(SystemExit):
                main(["--src", str(src), "--dst", str(root), "--clean"])


if __name__ == "__main__":
    unittest.main()
