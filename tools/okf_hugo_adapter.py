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
# CommonMark link reference definition: `[label]: target "title"`, optionally
# indented up to 3 spaces, with the target optionally wrapped in `<...>`.
REFERENCE_DEFINITION_RE = re.compile(
    r'^([ \t]{0,3}\[[^\]\n]+\]:[ \t]*)(<[^>\n]*>|\S+)'
    r'([ \t]+(?:"[^"\n]*"|\'[^\'\n]*\'|\([^)\n]*\)))?[ \t]*$',
    re.MULTILINE,
)
CODE_REGION_RE = re.compile(
    r"^(?P<fence>`{3,}|~{3,})[^\n]*\n.*?^(?P=fence)[ \t]*$|`[^`\n]+`",
    re.MULTILINE | re.DOTALL,
)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src = args.src.resolve()

    symlinked = find_symlinked_destination(args.dst)
    if symlinked is not None:
        raise SystemExit(f"destination path contains a symbolic link: {symlinked}")
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


def find_symlinked_destination(dst: Path) -> Path | None:
    """Check every lexical component of dst, below a trusted base, for a symlink.

    The trusted base is the working directory for a relative dst, or the
    filesystem root for an absolute one; components at or above it are not
    flagged. Every component of dst itself is checked regardless of whether
    it (or the full destination) already exists, so a symlinked ancestor
    cannot hide behind an already-existing resolved destination.
    """
    if dst.is_absolute():
        current = Path(dst.anchor)
        remaining_parts = dst.parts[1:]
    else:
        current = Path.cwd()
        remaining_parts = dst.parts

    for part in remaining_parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def check_generated_content(src: Path, dst: Path) -> int:
    if not dst.is_dir():
        print(f"generated content missing: {dst} does not exist", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory(prefix="okf-hugo-check-") as tmp:
        expected_dst = Path(tmp) / "content"
        generate_content(src, expected_dst)
        # dircmp defaults to shallow=True, comparing os.stat() signatures
        # (size and mtime) rather than bytes, so an equal-size file with a
        # matching mtime is classified as identical even when its content
        # differs. Force a deep, byte-level comparison.
        comparison = filecmp.dircmp(expected_dst, dst, shallow=False)
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
    check_output_collisions(documents)
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


def check_output_collisions(documents: list[OkfDocument]) -> None:
    """Reject documents whose generated file path or public permalink collide.

    OKF only reserves index.md and log.md, so e.g. `_index.md` is a valid
    concept filename that maps to the same generated path as `index.md`, and
    `foo.md` / `foo/index.md` map to the same Hugo permalink. Detect both
    before any destination file is created or overwritten.
    """
    by_output_path: dict[PurePosixPath, PurePosixPath] = {}
    by_permalink: dict[str, PurePosixPath] = {}
    for document in documents:
        output_path = output_relative_path(document.relative_path)
        existing = by_output_path.get(output_path)
        if existing is not None:
            raise FrontMatterError(
                f"generated output path collision between {existing} and "
                f"{document.relative_path}: both map to {output_path}"
            )
        by_output_path[output_path] = document.relative_path

        permalink = public_url_for_okf_path(document.relative_path)
        existing_permalink = by_permalink.get(permalink)
        if existing_permalink is not None:
            raise FrontMatterError(
                f"generated permalink collision between {existing_permalink} "
                f"and {document.relative_path}: both map to {permalink}"
            )
        by_permalink[permalink] = document.relative_path


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

    def replace_reference_definition(match: re.Match[str]) -> str:
        prefix, raw_target, title = match.groups()
        bracketed = raw_target.startswith("<") and raw_target.endswith(">")
        target = raw_target[1:-1] if bracketed else raw_target
        rewritten = rewrite_link_target(source_relative_path, target, okf_paths)
        if rewritten == target:
            return match.group(0)
        new_target = f"<{rewritten}>" if bracketed else rewritten
        return f"{prefix}{new_target}{title or ''}"

    def rewrite_segment(text: str) -> str:
        text = MARKDOWN_LINK_RE.sub(replace, text)
        return REFERENCE_DEFINITION_RE.sub(replace_reference_definition, text)

    segments: list[str] = []
    cursor = 0
    for code_match in CODE_REGION_RE.finditer(body):
        segments.append(rewrite_segment(body[cursor : code_match.start()]))
        segments.append(code_match.group(0))
        cursor = code_match.end()
    segments.append(rewrite_segment(body[cursor:]))
    return "".join(segments)


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
    if relative_path.name == "_index.md":
        # OKF reserves only index.md and log.md, so a standalone `_index.md`
        # concept is valid and must not be written to a literal `_index.md`
        # file: Hugo treats that filename as the section's own branch-bundle
        # page. Route it into a same-named directory instead, so it lands on
        # a plain leaf bundle at the path public_url_for_okf_path() already
        # advertises, rather than silently becoming the section home page.
        return relative_path.parent / "_index" / "index.md"
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
