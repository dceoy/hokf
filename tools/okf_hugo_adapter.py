#!/usr/bin/env python3
"""Generate disposable Hugo content from a canonical OKF v0.2 bundle."""

from __future__ import annotations

import argparse
import filecmp
import posixpath
import re
import shutil
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from tools.okf_common import (
        FrontMatterError,
        OkfDocument,
        dump_front_matter,
        read_documents,
    )
except ModuleNotFoundError:
    from okf_common import (  # type: ignore[no-redef]
        FrontMatterError,
        OkfDocument,
        dump_front_matter,
        read_documents,
    )


MARKDOWN_LINK_RE = re.compile(r"(!?\[[^\]]*\])\(([^)\s]+)(\s+\"[^\"]*\")?\)")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src = args.src.resolve()
    dst = args.dst.resolve()

    if not src.is_dir():
        raise SystemExit(f"source directory does not exist: {src}")
    if src == dst or src in dst.parents or dst in src.parents:
        raise SystemExit("source and destination directories must not overlap")
    if dst == Path(dst.anchor):
        raise SystemExit("destination must not be a filesystem root")

    try:
        if args.check:
            return check_generated_content(src, dst)
        if args.clean:
            clean_destination(dst)
        generated = generate_content(src, dst)
    except FrontMatterError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

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
        help="compare the destination with freshly generated output",
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
        comparison = filecmp.dircmp(expected_dst, dst)
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
    dst.mkdir(parents=True, exist_ok=True)
    resolved_dst = dst.resolve()

    for document in documents:
        output_path = dst / output_relative_path(document.relative_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if (
            not output_path.parent.resolve().is_relative_to(resolved_dst)
            or output_path.is_symlink()
        ):
            raise FrontMatterError(
                f"destination path escapes through a symbolic link: {output_path}"
            )
        output_path.write_text(render_document(document, okf_paths), encoding="utf-8")

    return len(documents)


def render_document(
    document: OkfDocument, okf_paths: set[PurePosixPath]
) -> str:
    metadata = hugo_metadata(document)
    body = rewrite_markdown_links(document.relative_path, document.body, okf_paths)
    rendered = f"{dump_front_matter(metadata)}\n{body}"
    return rendered if rendered.endswith("\n") else f"{rendered}\n"


def hugo_metadata(document: OkfDocument) -> dict[str, Any]:
    source_metadata = dict(document.metadata or {})
    if document.is_reserved:
        return {"type": "knowledge", **source_metadata}
    return source_metadata


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

    if link_path.startswith("/"):
        resolved = normalize_posix_path(PurePosixPath(link_path.lstrip("/")))
    else:
        resolved = normalize_posix_path(source_relative_path.parent / link_path)
    if resolved not in okf_paths:
        return target

    return public_url_for_okf_path(resolved) + suffix


def is_external_or_special_link(target: str) -> bool:
    return (
        "://" in target
        or target.startswith(("#", "mailto:", "tel:", "{{"))
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
        if child.is_symlink():
            child.unlink()
        elif child.is_dir():
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
