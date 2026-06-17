#!/usr/bin/env python3
"""Generate Hugo content from canonical OKF Markdown."""

from __future__ import annotations

import argparse
import filecmp
import posixpath
import re
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


FRONT_MATTER_DELIMITER = "---"
MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)\s]+)(\s+\"[^\"]*\")?\)")
TOP_LEVEL_YAML_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")


@dataclass(frozen=True)
class FrontMatterEntry:
    key: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class OkfDocument:
    source_path: Path
    relative_path: PurePosixPath
    front_matter: tuple[FrontMatterEntry, ...]
    body: str


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src = args.src.resolve()
    dst = args.dst.resolve()

    if not src.is_dir():
        raise SystemExit(f"source directory does not exist: {src}")
    if src == dst or src in dst.parents or dst in src.parents:
        raise SystemExit("source and destination directories must not overlap")

    if args.check:
        return check_generated_content(src, dst)

    if args.clean:
        clean_destination(dst)

    generated = generate_content(src, dst)
    print(f"generated {generated} file(s) from {src} to {dst}")
    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate Hugo content from OKF Markdown."
    )
    parser.add_argument("--src", type=Path, default=Path("okf"))
    parser.add_argument("--dst", type=Path, default=Path("site/content"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify generated output without changing the destination",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove existing destination files before generating",
    )
    return parser.parse_args(argv)


def check_generated_content(src: Path, dst: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="okf-hugo-check-") as tmp:
        expected_dst = Path(tmp) / "content"
        generate_content(src, expected_dst)
        comparison = filecmp.dircmp(expected_dst, dst, ignore=[".gitkeep"])
        differences = collect_directory_differences(comparison)

    if differences:
        for difference in differences:
            print(difference, file=sys.stderr)
        return 1

    print(f"generated content is up to date in {dst}")
    return 0


def generate_content(src: Path, dst: Path) -> int:
    documents = read_documents(src)
    okf_paths = {document.relative_path for document in documents}
    generated_count = 0

    for document in documents:
        output_path = dst / output_relative_path(document.relative_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(render_document(document, okf_paths), encoding="utf-8")
        generated_count += 1

    return generated_count


def read_documents(src: Path) -> list[OkfDocument]:
    documents = []
    for source_path in sorted(src.rglob("*.md")):
        relative_path = PurePosixPath(source_path.relative_to(src).as_posix())
        front_matter, body = split_front_matter(source_path.read_text(encoding="utf-8"))
        documents.append(
            OkfDocument(
                source_path=source_path,
                relative_path=relative_path,
                front_matter=front_matter,
                body=body,
            )
        )
    return documents


def split_front_matter(markdown: str) -> tuple[tuple[FrontMatterEntry, ...], str]:
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        return (), markdown

    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {FRONT_MATTER_DELIMITER, "..."}:
            front_matter_lines = lines[1:index]
            body = "".join(lines[index + 1 :])
            return parse_front_matter_entries(front_matter_lines), body

    return (), markdown


def parse_front_matter_entries(lines: list[str]) -> tuple[FrontMatterEntry, ...]:
    entries: list[FrontMatterEntry] = []
    current_key: str | None = None
    current_lines: list[str] = []

    for line in lines:
        match = TOP_LEVEL_YAML_KEY_RE.match(line)
        if match and not line.startswith((" ", "\t")):
            if current_key is not None:
                entries.append(
                    FrontMatterEntry(current_key, tuple(current_lines))
                )
            current_key = match.group(1)
            current_lines = [line]
            continue

        if current_key is None:
            current_key = ""
        current_lines.append(line)

    if current_key is not None:
        entries.append(FrontMatterEntry(current_key, tuple(current_lines)))

    return tuple(entries)


def render_document(document: OkfDocument, okf_paths: set[PurePosixPath]) -> str:
    front_matter = render_front_matter(document.front_matter)
    body = rewrite_markdown_links(document.relative_path, document.body, okf_paths)
    rendered = f"{front_matter}\n{body}"
    if not rendered.endswith("\n"):
        rendered += "\n"
    return rendered


def render_front_matter(entries: tuple[FrontMatterEntry, ...]) -> str:
    okf_type = extract_okf_type(entries)
    rendered_entries: list[str] = ["type: knowledge\n"]
    has_params = False

    for entry in entries:
        if entry.key == "type":
            continue
        if entry.key == "params":
            has_params = True
            rendered_entries.extend(merge_okf_type_into_params(entry, okf_type))
            continue
        rendered_entries.extend(entry.lines)

    if okf_type and not has_params:
        rendered_entries.extend(["params:\n", f"  okf_type: {okf_type}\n"])

    return (
        FRONT_MATTER_DELIMITER
        + "\n"
        + "".join(rendered_entries).rstrip()
        + "\n"
        + FRONT_MATTER_DELIMITER
        + "\n"
    )


def extract_okf_type(entries: tuple[FrontMatterEntry, ...]) -> str | None:
    for entry in entries:
        if entry.key != "type" or not entry.lines:
            continue
        match = TOP_LEVEL_YAML_KEY_RE.match(entry.lines[0])
        if not match:
            continue
        value = match.group(2).strip()
        return value or None
    return None


def merge_okf_type_into_params(
    entry: FrontMatterEntry, okf_type: str | None
) -> tuple[str, ...]:
    if okf_type is None:
        return entry.lines

    lines = list(entry.lines)
    if len(lines) == 1 and lines[0].strip() == "params:":
        return tuple([lines[0], f"  okf_type: {okf_type}\n"])

    for line in lines[1:]:
        if re.match(r"^\s+okf_type:", line):
            return tuple(lines)

    return tuple([lines[0], f"  okf_type: {okf_type}\n", *lines[1:]])


def rewrite_markdown_links(
    source_relative_path: PurePosixPath,
    body: str,
    okf_paths: set[PurePosixPath],
) -> str:
    def replace(match: re.Match[str]) -> str:
        label, target, title = match.groups()
        rewritten = rewrite_link_target(source_relative_path, target, okf_paths)
        if rewritten == target:
            return match.group(0)
        return f"{label}({rewritten}{title or ''})"

    return MARKDOWN_LINK_RE.sub(replace, body)


def rewrite_link_target(
    source_relative_path: PurePosixPath,
    target: str,
    okf_paths: set[PurePosixPath],
) -> str:
    if is_external_or_special_link(target):
        return target

    link_path, suffix = split_link_suffix(target)
    if not link_path.endswith(".md"):
        return target

    resolved = normalize_posix_path(source_relative_path.parent / link_path)
    if resolved not in okf_paths:
        return target

    return public_url_for_okf_path(resolved) + suffix


def is_external_or_special_link(target: str) -> bool:
    return (
        "://" in target
        or target.startswith(("#", "/", "mailto:", "tel:"))
        or target.startswith("<")
    )


def split_link_suffix(target: str) -> tuple[str, str]:
    suffix_start = len(target)
    for marker in ("#", "?"):
        marker_index = target.find(marker)
        if marker_index != -1:
            suffix_start = min(suffix_start, marker_index)
    return target[:suffix_start], target[suffix_start:]


def normalize_posix_path(path: PurePosixPath) -> PurePosixPath:
    normalized = posixpath.normpath(path.as_posix())
    if normalized == ".":
        return PurePosixPath("")
    return PurePosixPath(normalized)


def output_relative_path(relative_path: PurePosixPath) -> PurePosixPath:
    if relative_path.name == "index.md":
        return relative_path.parent / "_index.md"
    return relative_path


def public_url_for_okf_path(relative_path: PurePosixPath) -> str:
    if relative_path.name == "index.md":
        url_path = relative_path.parent.as_posix()
    else:
        url_path = relative_path.with_suffix("").as_posix()

    if url_path in {"", "."}:
        return "/"
    return f"/{url_path.strip('/')}/"


def clean_destination(dst: Path) -> None:
    if not dst.exists():
        return
    for child in dst.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def collect_directory_differences(comparison: filecmp.dircmp) -> list[str]:
    differences: list[str] = []
    for name in comparison.left_only:
        differences.append(f"missing from destination: {name}")
    for name in comparison.right_only:
        differences.append(f"unexpected in destination: {name}")
    for name in comparison.diff_files:
        differences.append(f"content differs: {name}")
    for name, subcomparison in comparison.subdirs.items():
        for difference in collect_directory_differences(subcomparison):
            differences.append(f"{name}/{difference}")
    return differences


if __name__ == "__main__":
    raise SystemExit(main())
