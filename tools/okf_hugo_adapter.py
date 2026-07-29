#!/usr/bin/env python3
"""Generate disposable Hugo content from a canonical OKF v0.2 bundle."""

from __future__ import annotations

import argparse
import bisect
import filecmp
import html
import html.entities
import posixpath
import re
import shutil
import string
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import quote, unquote

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


# CommonMark link reference definition candidate, optionally indented up to
# 3 spaces past a line's block-container prefix. Labels, destinations, and
# titles are parsed structurally below. Matched per line at a specific
# offset (see find_reference_definition_candidates), so this intentionally
# has no "^" anchor.
REFERENCE_DEFINITION_CANDIDATE_RE = re.compile(r"[ \t]{0,3}\[")
# A leaf block that closes at end-of-line, so no paragraph is left open for
# iter_reference_definitions() to consider on the next line (whether a
# "-"-only run is read as a thematic break or a setext underline, both close
# whatever came before it, so that ambiguity does not matter here).
ATX_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]|$)")
THEMATIC_BREAK_RE = re.compile(
    r"^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:_[ \t]*){3,}|(?:\*[ \t]*){3,})$"
)
# A setext heading underline: one or more "=" (always H1) or "-" (always H2)
# characters with no internal spaces. Only closes a paragraph when one is
# actually open (see iter_reference_definitions()) - otherwise a bare "-" or
# "=" run is ordinary paragraph text. "-" runs of 3+ are already covered by
# THEMATIC_BREAK_RE above with the same result, so this only adds the "="
# case and short "-"/"--" runs that are too short to be a thematic break.
SETEXT_UNDERLINE_RE = re.compile(r"^[ \t]{0,3}(?:=+|-+)[ \t]*$")
BACKTICK_RUN_RE = re.compile(r"`+")
INLINE_BLOCK_BREAK_RE = re.compile(r"\r?\n[ \t]*\r?\n")
BLANK_LINE_RE = re.compile(r"(?:\r\n|\r|\n)[ \t]*(?:\r\n|\r|\n)")
CHARACTER_REFERENCE_RE = re.compile(
    r"&(?:#[0-9]{1,7}|#[xX][0-9A-Fa-f]{1,6}|[A-Za-z][A-Za-z0-9]*);"
)
FENCE_OPEN_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})")
INDENTED_LINE_RE = re.compile(r"^(?: {4}|\t)")
CONTAINER_LIST_MARKER_RE = re.compile(r"(?:[-+*]|\d{1,9}[.)])")
ASCII_PUNCTUATION = frozenset(string.punctuation)
# CommonMark raw HTML has seven block forms plus an inline tag grammar. Keep
# these fragments local so validation and generation share the same ranges.
HTML_LITERAL_BLOCK_START_RE = re.compile(
    r"^ {0,3}<(?:pre|script|style|textarea)(?=[ \t>]|$)",
    re.IGNORECASE,
)
HTML_LITERAL_BLOCK_END_RE = re.compile(
    r"</(?:pre|script|style|textarea)>",
    re.IGNORECASE,
)
HTML_BLOCK_TAG_NAMES = """
address article aside base basefont blockquote body caption center col colgroup dd
details dialog dir div dl dt fieldset figcaption figure footer form frame frameset
h1 h2 h3 h4 h5 h6 head header hr html iframe legend li link main menu menuitem nav
noframes ol optgroup option p param search section summary table tbody td tfoot th
thead title tr track ul
""".split()
HTML_BLOCK_TAG_START_RE = re.compile(
    rf"^ {{0,3}}</?(?:{'|'.join(HTML_BLOCK_TAG_NAMES)})(?=[ \t/>]|$)",
    re.IGNORECASE,
)
HTML_LINE_ENDING_PATTERN = r"(?:\r\n|\r|\n)"
HTML_OPTIONAL_SPACE_PATTERN = (
    rf"[ \t]*(?:{HTML_LINE_ENDING_PATTERN}[ \t]*)?"
)
HTML_REQUIRED_SPACE_PATTERN = (
    rf"(?:[ \t]+(?:{HTML_LINE_ENDING_PATTERN}[ \t]*)?"
    rf"|[ \t]*{HTML_LINE_ENDING_PATTERN}[ \t]*)"
)
HTML_ATTRIBUTE_NAME_PATTERN = r"[A-Za-z_:][A-Za-z0-9_.:-]*"
HTML_ATTRIBUTE_VALUE_PATTERN = r"""(?:"[^"]*"|'[^']*'|[^ "'=<>`]+)"""
HTML_OPEN_TAG_PATTERN = (
    r"<[A-Za-z][A-Za-z0-9-]*"
    rf"(?:{HTML_REQUIRED_SPACE_PATTERN}{HTML_ATTRIBUTE_NAME_PATTERN}"
    rf"(?:{HTML_OPTIONAL_SPACE_PATTERN}={HTML_OPTIONAL_SPACE_PATTERN}"
    rf"{HTML_ATTRIBUTE_VALUE_PATTERN})?)*"
    rf"{HTML_OPTIONAL_SPACE_PATTERN}/?>"
)
HTML_CLOSING_TAG_PATTERN = (
    r"</[A-Za-z][A-Za-z0-9-]*"
    rf"{HTML_OPTIONAL_SPACE_PATTERN}>"
)
HTML_TAG_RE = re.compile(
    rf"(?:{HTML_OPEN_TAG_PATTERN}|{HTML_CLOSING_TAG_PATTERN})"
)
RAW_HTML_RE = re.compile(
    rf"(?:"
    r"<!--(?:>|->|(?:(?!-->).)*-->)"
    r"|<\?(?:(?!\?>).)*\?>"
    r"|<!\[CDATA\[(?:(?!\]\]>).)*\]\]>"
    r"|<![A-Za-z][^>]*>"
    rf"|{HTML_OPEN_TAG_PATTERN}"
    rf"|{HTML_CLOSING_TAG_PATTERN}"
    r")",
    re.DOTALL,
)
URI_AUTOLINK_RE = re.compile(
    r"<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\x00-\x20<>]*>"
)


@dataclass(frozen=True)
class InlineLink:
    start: int
    end: int
    prefix: str
    raw_target: str
    suffix: str
    target_start: int
    target_end: int


@dataclass(frozen=True)
class ReferenceDefinition:
    start: int
    end: int
    prefix: str
    raw_target: str
    suffix: str
    label: str


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
    """Check every effective component of dst, below a trusted base, for a symlink.

    The trusted base is the working directory for a relative dst, or the
    filesystem root for an absolute one; components at or above it are not
    flagged. dst's components are first normalized lexically (resolving ``..``
    against components already walked here, without touching the filesystem)
    so that a nonexistent component ahead of a ``..`` cannot hide a symlink
    that only becomes visible once the path is later resolved. Every effective
    component is checked regardless of whether it (or the full destination)
    already exists, so a symlinked ancestor cannot hide behind an
    already-existing resolved destination.
    """
    if dst.is_absolute():
        base = Path(dst.anchor)
        remaining_parts = dst.parts[1:]
    else:
        base = Path.cwd()
        remaining_parts = dst.parts

    effective_parts: list[str] = []
    for part in remaining_parts:
        if part == "..":
            if not effective_parts:
                raise SystemExit(f"destination escapes its base directory: {dst}")
            effective_parts.pop()
            continue
        effective_parts.append(part)
        current = base.joinpath(*effective_parts)
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


# OKF concept fields that are intentionally mapped onto Hugo's own
# top-level front matter meaning (page-kind dispatch, display title and
# description). A tuple, not a set: dump_front_matter() writes keys in
# insertion order, and a set's iteration order is salted per process, which
# would make repeated `--check` runs across processes disagree on identical
# input.
HUGO_TOP_LEVEL_KEYS = ("type", "title", "description")


def hugo_metadata(document: OkfDocument) -> dict[str, Any]:
    source_metadata = dict(document.metadata or {})
    metadata: dict[str, Any] = {
        key: source_metadata.pop(key)
        for key in HUGO_TOP_LEVEL_KEYS
        if key in source_metadata
    }
    if document.is_reserved:
        metadata["type"] = "knowledge"
    if source_metadata:
        # Namespace every other producer-defined key (including ones that
        # look like Hugo-reserved fields such as draft/url/slug/build) under
        # params.okf so it cannot acquire unintended Hugo publishing
        # semantics; Hugo flattens a front-matter `params` table directly
        # onto Page.Params, so this is read back as .Params.okf.<key>.
        metadata["params"] = {"okf": source_metadata}
    return metadata


def find_code_block_spans(body: str) -> list[tuple[int, int]]:
    """Return merged (start, end) character spans of fenced/indented code.

    Covers fenced code blocks (including fences nested in block quotes and
    list items) and indented code blocks only, not inline code spans, so
    callers that need to know where a leaf block closes (and therefore that
    no paragraph is left open afterward) don't have to distinguish those
    block-level spans from unrelated inline backtick runs. Active
    list-container indentation is retained across blank lines so ordinary
    list continuations remain prose while content indented four additional
    columns is recognized as a code block.
    """
    spans: list[tuple[int, int]] = []
    lines = body.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    n = len(lines)
    prev_blank = True
    last_nonblank_text = ""
    active_list_tokens: tuple[tuple[str, int], ...] = ()
    i = 0
    while i < n:
        line = lines[i]
        text = line.rstrip("\r\n")

        container_tokens, content_start = parse_block_container_prefix(text)
        container_content = text[content_start:]
        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        line_starts_list = any(kind == "list" for kind, _ in container_tokens)
        line_starts_nested_list = False

        if active_list_tokens and active_content is None:
            if line_starts_list:
                # A sibling or less deeply nested list marker starts a new
                # active prefix that supersedes the previous item.
                active_list_tokens = container_tokens
            elif container_content.strip() != "" and prev_blank:
                # A non-indented block after a blank line ends the list.
                active_list_tokens = ()
        elif active_list_tokens and active_content is not None:
            nested_tokens, _ = parse_block_container_prefix(active_content)
            if any(kind == "list" for kind, _ in nested_tokens):
                line_starts_nested_list = True
                active_list_tokens += nested_tokens
        elif line_starts_list:
            active_list_tokens = container_tokens

        fence_match = FENCE_OPEN_RE.match(text[content_start:])
        if fence_match:
            fence_run = fence_match.group(1)
            fence_char, fence_len = fence_run[0], len(fence_run)
            close_re = re.compile(
                rf"^[ \t]{{0,3}}{re.escape(fence_char)}{{{fence_len},}}[ \t]*$"
            )
            j = i + 1
            while j < n:
                candidate = strip_block_container_prefix(
                    lines[j].rstrip("\r\n"), container_tokens
                )
                if candidate is not None and close_re.match(candidate):
                    break
                j += 1
            end_index = j if j < n else n - 1
            spans.append((offsets[i], offsets[end_index] + len(lines[end_index])))
            i = end_index + 1
            prev_blank = False
            last_nonblank_text = text
            continue

        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        indented_content = (
            active_content if active_content is not None else container_content
        )
        indented_container_tokens = (
            active_list_tokens if active_content is not None else container_tokens
        )
        if (
            INDENTED_LINE_RE.match(indented_content)
            and prev_blank
            and not line_starts_nested_list
            and (
                active_content is not None
                or not INDENTED_LINE_RE.match(last_nonblank_text)
            )
        ):
            last_content = i
            j = i
            while j < n:
                candidate = strip_block_container_prefix(
                    lines[j].rstrip("\r\n"), indented_container_tokens
                )
                if candidate is None:
                    _, candidate_start = parse_block_container_prefix(
                        lines[j].rstrip("\r\n")
                    )
                    candidate_container_content = lines[j].rstrip("\r\n")[
                        candidate_start:
                    ]
                    if candidate_container_content.strip() == "":
                        j += 1
                        continue
                    break
                if INDENTED_LINE_RE.match(candidate):
                    last_content = j
                    j += 1
                    continue
                if candidate == "":
                    j += 1
                    continue
                break
            spans.append(
                (offsets[i], offsets[last_content] + len(lines[last_content]))
            )
            i = last_content + 1
            prev_blank = False
            last_nonblank_text = lines[last_content].rstrip("\r\n")
            continue

        if container_content.strip() != "":
            last_nonblank_text = text
        prev_blank = container_content.strip() == ""
        i += 1

    return merge_spans(spans)


def find_code_spans(body: str) -> list[tuple[int, int]]:
    """Return merged (start, end) character spans of Markdown code regions.

    Covers everything find_code_block_spans() does plus inline code spans
    delimited by equal-length backtick runs (including multiline spans), so
    link rewriting can skip all of them.
    """
    block_spans = find_code_block_spans(body)
    spans = list(block_spans)

    def in_block(start: int, end: int) -> bool:
        return any(
            start < block_end and end > block_start
            for block_start, block_end in block_spans
        )

    # CommonMark code spans open and close with backtick strings of exactly
    # the same length. Runs of other lengths are literal content, and line
    # endings are allowed inside a span.
    runs = list(BACKTICK_RUN_RE.finditer(body))
    run_index = 0
    while run_index < len(runs):
        opener = runs[run_index]
        if in_block(opener.start(), opener.end()):
            run_index += 1
            continue

        closer_index = run_index + 1
        matched = False
        while closer_index < len(runs):
            closer = runs[closer_index]
            between = body[opener.end() : closer.start()]
            crosses_block_region = any(
                opener.end() <= block_start < closer.start()
                for block_start, _ in block_spans
            )
            if crosses_block_region or INLINE_BLOCK_BREAK_RE.search(between):
                break
            if (
                not in_block(closer.start(), closer.end())
                and len(closer.group(0)) == len(opener.group(0))
            ):
                spans.append((opener.start(), closer.end()))
                run_index = closer_index + 1
                matched = True
                break
            closer_index += 1
        if not matched:
            run_index += 1

    return merge_spans(spans)


def merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def html_block_start(
    body: str,
    line_start: int,
    content_start: int,
    content: str,
    type_seven_allowed: bool,
) -> tuple[str, str | re.Pattern[str] | None] | None:
    """Classify a CommonMark HTML block start on one normalized line."""
    indent = len(content) - len(content.lstrip(" "))
    if indent > 3:
        return None
    candidate = content[indent:]
    candidate_start = line_start + content_start + indent

    if HTML_LITERAL_BLOCK_START_RE.match(content):
        return ("pattern", HTML_LITERAL_BLOCK_END_RE)
    if candidate.startswith("<!--"):
        return ("token", "-->")
    if candidate.startswith("<?"):
        return ("token", "?>")
    if (
        candidate.startswith("<!")
        and len(candidate) > 2
        and candidate[2].isascii()
        and candidate[2].isalpha()
    ):
        return ("token", ">")
    if candidate.startswith("<![CDATA["):
        return ("token", "]]>")
    if HTML_BLOCK_TAG_START_RE.match(content):
        return ("blank", None)

    tag_match = HTML_TAG_RE.match(body, candidate_start)
    line_end = line_start + len(content) + content_start
    if (
        type_seven_allowed
        and tag_match is not None
        and tag_match.end() <= line_end
        and body[tag_match.end() : line_end].strip(" \t") == ""
    ):
        return ("blank", None)
    return None


def find_html_block_spans(
    body: str, code_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Return CommonMark HTML block source ranges (not inline raw HTML).

    Kept separate from find_raw_html_spans() so callers that need to know
    where a leaf block closes (and therefore that no paragraph is left open
    afterward) don't have to distinguish these block-level spans from
    unrelated inline raw-HTML tags.
    """
    lines = body.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    block_spans: list[tuple[int, int]] = []
    # `type_seven_allowed` tracks whether the previous *effective* line (its
    # active block-quote/list container prefix stripped) was blank, so a
    # type-7 HTML block cannot interrupt an already-open paragraph.
    # `prev_container_tokens` tracks that previous line's effective container
    # so entering a new container (which always starts fresh, even mid
    # paragraph in the enclosing context) also permits a type-7 block.
    type_seven_allowed = True
    prev_container_tokens: tuple[tuple[str, int], ...] = ()
    active_list_tokens: tuple[tuple[str, int], ...] = ()
    prev_blank = True
    code_line_index = 0
    i = 0
    while i < len(lines):
        line_end = offsets[i] + len(lines[i])
        while (
            code_line_index < len(code_spans)
            and code_spans[code_line_index][1] <= offsets[i]
        ):
            code_line_index += 1
        if (
            code_line_index < len(code_spans)
            and offsets[i] < code_spans[code_line_index][1]
            and line_end > code_spans[code_line_index][0]
        ):
            code_start, code_end = code_spans[code_line_index]
            type_seven_allowed = (
                code_start <= offsets[i] and code_end == line_end
            )
            i += 1
            continue

        text = lines[i].rstrip("\r\n")
        container_tokens, content_start = parse_block_container_prefix(text)
        container_content = text[content_start:]
        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        line_starts_list = any(kind == "list" for kind, _ in container_tokens)

        if active_list_tokens and active_content is None:
            if line_starts_list:
                # A sibling or less deeply nested list marker starts a new
                # active prefix that supersedes the previous item.
                active_list_tokens = container_tokens
            elif container_content.strip() != "" and prev_blank:
                # A non-indented block after a blank line ends the list.
                active_list_tokens = ()
        elif active_list_tokens and active_content is not None:
            nested_tokens, _ = parse_block_container_prefix(active_content)
            if any(kind == "list" for kind, _ in nested_tokens):
                active_list_tokens += nested_tokens
        elif line_starts_list:
            active_list_tokens = container_tokens

        # Recompute against the (possibly just-updated) active list prefix so
        # indentation-only list continuation lines are normalized the same
        # way `find_code_spans()` normalizes them for fence/indented-code
        # detection.
        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        indented_content = (
            active_content if active_content is not None else container_content
        )
        indented_container_tokens = (
            active_list_tokens if active_content is not None else container_tokens
        )
        indented_content_start = len(text) - len(indented_content)

        container_changed = indented_container_tokens != prev_container_tokens
        start = html_block_start(
            body,
            offsets[i],
            indented_content_start,
            indented_content,
            type_seven_allowed or container_changed,
        )

        prev_blank = container_content.strip() == ""

        if start is None:
            type_seven_allowed = indented_content.strip() == ""
            prev_container_tokens = indented_container_tokens
            i += 1
            continue

        end_kind, end_condition = start
        end_index = i
        while True:
            candidate_text = lines[end_index].rstrip("\r\n")
            candidate = strip_block_container_prefix(
                candidate_text, indented_container_tokens
            )
            assert candidate is not None

            ended = False
            if end_kind == "pattern":
                assert isinstance(end_condition, re.Pattern)
                ended = end_condition.search(candidate) is not None
            elif end_kind == "token":
                assert isinstance(end_condition, str)
                ended = end_condition in candidate
            if ended or end_index + 1 >= len(lines):
                break

            next_text = lines[end_index + 1].rstrip("\r\n")
            next_candidate = strip_block_container_prefix(
                next_text, indented_container_tokens
            )
            if next_candidate is None or (
                end_kind == "blank" and next_candidate.strip() == ""
            ):
                break
            end_index += 1

        block_spans.append(
            (offsets[i], offsets[end_index] + len(lines[end_index]))
        )
        type_seven_allowed = True
        prev_container_tokens = indented_container_tokens
        prev_blank = False
        i = end_index + 1

    return merge_spans(block_spans)


def find_raw_html_spans(
    body: str, code_spans: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Return CommonMark HTML block and inline raw-HTML source ranges."""
    block_spans = find_html_block_spans(body, code_spans)
    spans = list(block_spans)
    excluded = merge_spans(block_spans + code_spans)
    excluded_index = 0
    for match in RAW_HTML_RE.finditer(body):
        while (
            excluded_index < len(excluded)
            and excluded[excluded_index][1] <= match.start()
        ):
            excluded_index += 1
        if (
            excluded_index < len(excluded)
            and match.end() > excluded[excluded_index][0]
        ):
            continue
        spans.append((match.start(), match.end()))
    return merge_spans(spans)


def find_link_exclusion_spans(body: str) -> list[tuple[int, int]]:
    code_spans = find_code_spans(body)
    return merge_spans(code_spans + find_raw_html_spans(body, code_spans))


def find_bracket_precedence_spans(
    body: str,
    excluded_spans: list[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Return inline ranges whose brackets do not participate in links.

    CommonMark resolves code spans, autolinks, and raw HTML before link
    brackets. The ordinary link exclusions already cover code and raw HTML;
    add URI autolinks that do not overlap either kind of excluded range.
    """
    excluded = (
        find_link_exclusion_spans(body)
        if excluded_spans is None
        else excluded_spans
    )
    spans = list(excluded)
    for match in URI_AUTOLINK_RE.finditer(body):
        if any(
            match.start() < span_end and match.end() > span_start
            for span_start, span_end in excluded
        ):
            continue
        spans.append((match.start(), match.end()))
    return merge_spans(spans)


def consume_indent(text: str, cursor: int, width: int) -> int | None:
    """Consume width columns of spaces/tabs from cursor."""
    columns = 0
    while cursor < len(text) and columns < width:
        if text[cursor] == " ":
            columns += 1
        elif text[cursor] == "\t":
            columns += 4 - (columns % 4)
        else:
            return None
        cursor += 1
    return cursor if columns == width else None


def parse_block_container_prefix(text: str) -> tuple[tuple[tuple[str, int], ...], int]:
    """Return block-quote/list container tokens and the content start.

    List tokens retain their content indentation so continuation lines can be
    normalized before testing for a closing fence.
    """
    tokens: list[tuple[str, int]] = []
    cursor = 0
    while cursor < len(text):
        marker_start = cursor
        indent = 0
        while (
            cursor < len(text)
            and text[cursor] == " "
            and indent < 3
        ):
            cursor += 1
            indent += 1

        if cursor < len(text) and text[cursor] == ">":
            cursor += 1
            if cursor < len(text) and text[cursor] in " \t":
                cursor += 1
            tokens.append(("quote", 0))
            continue

        list_match = CONTAINER_LIST_MARKER_RE.match(text, cursor)
        if list_match is not None:
            marker_end = list_match.end()
            whitespace_end = marker_end
            while (
                whitespace_end < len(text)
                and text[whitespace_end] in " \t"
                and whitespace_end - marker_end < 4
            ):
                whitespace_end += 1
            if whitespace_end > marker_end:
                tokens.append(("list", whitespace_end - marker_start))
                cursor = whitespace_end
                continue

        cursor = marker_start
        break
    return tuple(tokens), cursor


def strip_block_container_prefix(
    text: str, tokens: tuple[tuple[str, int], ...]
) -> str | None:
    """Strip the opener's block-container prefix from a continuation line."""
    cursor = 0
    for kind, width in tokens:
        if kind == "quote":
            indent = 0
            while cursor < len(text) and text[cursor] == " " and indent < 3:
                cursor += 1
                indent += 1
            if cursor >= len(text) or text[cursor] != ">":
                return None
            cursor += 1
            if cursor < len(text) and text[cursor] in " \t":
                cursor += 1
        else:
            next_cursor = consume_indent(text, cursor, width)
            if next_cursor is None:
                return None
            cursor = next_cursor
    return text[cursor:]


def unwrap_angle_brackets(target: str) -> str:
    if target.startswith("<") and target.endswith(">"):
        return target[1:-1]
    return target


def parse_inline_link_tail(
    body: str, label_start: int, destination_start: int
) -> InlineLink | None:
    """Parse the destination/title/")" following a "](" and build an
    InlineLink, or return None if no valid inline link tail follows.
    """
    cursor = consume_link_whitespace(body, destination_start)
    target_start = cursor
    target_end = consume_link_destination(body, cursor, allow_empty=True)
    if target_end is None:
        return None
    cursor = target_end
    whitespace_start = cursor
    cursor = consume_link_whitespace(body, cursor)

    if cursor > whitespace_start and cursor < len(body) and body[cursor] != ")":
        title_end = consume_link_title(body, cursor)
        if title_end is None:
            return None
        cursor = title_end
        cursor = consume_link_whitespace(body, cursor)

    if cursor >= len(body) or body[cursor] != ")":
        return None

    return InlineLink(
        start=label_start,
        end=cursor + 1,
        prefix=body[label_start:target_start],
        raw_target=body[target_start:target_end],
        suffix=body[target_end : cursor + 1],
        target_start=target_start,
        target_end=target_end,
    )


def is_backslash_escaped(body: str, index: int) -> bool:
    """Return whether the character at ``index`` follows an odd slash run."""
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and body[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def is_image_opener(body: str, bracket_index: int) -> bool:
    """Return whether ``body[bracket_index]`` starts a CommonMark image."""
    bang_index = bracket_index - 1
    return (
        bang_index >= 0
        and body[bang_index] == "!"
        and not is_backslash_escaped(body, bang_index)
    )


def find_inline_link_openers(
    body: str,
    excluded_spans: list[tuple[int, int]] | None = None,
) -> list[InlineLink]:
    """Locate and parse CommonMark inline links and images.

    Brackets are tracked with a stack so a "]" always matches the nearest
    unmatched "[", per CommonMark's bracket-matching algorithm. When a "["
    (not "![") successfully forms a link, every older still-open "[" is
    deactivated so it cannot itself become a link, which is what makes
    `[outer [inner](child.md)](other.md)` resolve to the innermost link only.
    Images do not deactivate older openers, since `[![badge](badge.svg)]
    (target.md)` is a real link around a real image in CommonMark. Nested
    brackets (`[see [child]](child.md)`) and backslash-escaped delimiters
    (`[child \\]](child.md)`) inside a label are honored. Soft line endings
    are allowed inside labels, but a blank line invalidates any label
    currently open across it.
    """
    links: list[InlineLink] = []
    precedence_spans = find_bracket_precedence_spans(body, excluded_spans)
    precedence_index = 0
    length = len(body)
    stack: list[dict[str, Any]] = []
    index = 0
    while index < length:
        while (
            precedence_index < len(precedence_spans)
            and precedence_spans[precedence_index][1] <= index
        ):
            precedence_index += 1
        if (
            precedence_index < len(precedence_spans)
            and precedence_spans[precedence_index][0] <= index
        ):
            span_end = precedence_spans[precedence_index][1]
            if BLANK_LINE_RE.search(body, index, span_end):
                for entry in stack:
                    entry["active"] = False
            index = span_end
            continue

        char = body[index]
        if (
            char == "\\"
            and index + 1 < length
            and body[index + 1] not in "\r\n"
        ):
            index += 2
            continue
        if char in "\r\n":
            if BLANK_LINE_RE.match(body, index):
                for entry in stack:
                    entry["active"] = False
            next_line = consume_line_ending(body, index)
            assert next_line is not None
            index = next_line
            continue
        if char == "[":
            image_opener = is_image_opener(body, index)
            label_start = index - 1 if image_opener else index
            stack.append(
                {
                    "label_start": label_start,
                    "is_image": image_opener,
                    "active": True,
                }
            )
            index += 1
            continue
        if char == "]":
            if not stack:
                index += 1
                continue
            entry = stack.pop()
            if (
                entry["active"]
                and index + 1 < length
                and body[index + 1] == "("
            ):
                link = parse_inline_link_tail(body, entry["label_start"], index + 2)
                if link is not None:
                    links.append(link)
                    if not entry["is_image"]:
                        for older in stack:
                            older["active"] = False
                    index = link.end
                    continue
            index += 1
            continue
        index += 1

    links.sort(key=lambda link: link.start)
    kept: list[InlineLink] = []
    for link in links:
        if any(
            other.start <= link.start and link.end <= other.end and other is not link
            for other in links
        ):
            continue
        kept.append(link)
    return kept


def find_reference_definition_candidates(
    body: str,
) -> list[tuple[int, int, tuple[tuple[str, int], ...]]]:
    """Return (definition_start, label_start, container_tokens) per line.

    CommonMark parses link reference definitions inside block quotes and
    list items and applies them to the whole document, so a candidate may be
    preceded by a block-container prefix in addition to up to 3 spaces.
    """
    candidates: list[tuple[int, int, tuple[tuple[str, int], ...]]] = []
    offset = 0
    for line in body.splitlines(keepends=True):
        text = line.rstrip("\r\n")
        tokens, content_start = parse_block_container_prefix(text)
        match = REFERENCE_DEFINITION_CANDIDATE_RE.match(text, content_start)
        if match is not None:
            candidates.append(
                (offset + match.start(), offset + match.end() - 1, tokens)
            )
        offset += len(line)
    return candidates


def find_reference_definition_openers(
    body: str,
) -> list[tuple[int, int, tuple[tuple[str, int], ...], str]]:
    """Locate CommonMark reference labels; return (start, after-colon, tokens,
    label).

    Reference labels may contain backslash-escaped punctuation and one line
    ending, but not an unescaped opening bracket or an empty/whitespace-only
    label. ``tokens`` is the opening line's block-container prefix, used to
    recognize repeated prefixes on any destination/title continuation line.
    ``label`` is the raw (unnormalized) source text between the brackets.
    """
    openers: list[tuple[int, int, tuple[tuple[str, int], ...], str]] = []
    for definition_start, label_start, tokens in find_reference_definition_candidates(
        body
    ):
        cursor = label_start + 1
        label_characters = 0
        has_non_whitespace = False
        line_endings = 0
        while cursor < len(body):
            char = body[cursor]
            if (
                char == "\\"
                and cursor + 1 < len(body)
                and body[cursor + 1] in ASCII_PUNCTUATION
            ):
                label_characters += 1
                has_non_whitespace = True
                cursor += 2
                continue
            if char == "[":
                break
            if char == "]":
                if (
                    label_characters <= 999
                    and has_non_whitespace
                    and cursor + 1 < len(body)
                    and body[cursor + 1] == ":"
                ):
                    openers.append(
                        (
                            definition_start,
                            cursor + 2,
                            tokens,
                            body[label_start + 1 : cursor],
                        )
                    )
                break
            if char in "\r\n":
                if line_endings == 1:
                    break
                next_line = consume_line_ending(body, cursor)
                assert next_line is not None
                line_endings += 1
                label_characters += 1
                cursor = next_line
                continue
            label_characters += 1
            has_non_whitespace = has_non_whitespace or not char.isspace()
            if label_characters > 999:
                break
            cursor += 1
    return openers


def consume_link_whitespace(body: str, cursor: int) -> int:
    """Consume spaces/tabs and at most one CommonMark line ending."""
    while cursor < len(body) and body[cursor] in " \t":
        cursor += 1
    if cursor < len(body) and body[cursor] in "\r\n":
        if (
            body[cursor] == "\r"
            and cursor + 1 < len(body)
            and body[cursor + 1] == "\n"
        ):
            cursor += 2
        else:
            cursor += 1
        while cursor < len(body) and body[cursor] in " \t":
            cursor += 1
    return cursor


def consume_spaces_and_tabs(body: str, cursor: int) -> int:
    while cursor < len(body) and body[cursor] in " \t":
        cursor += 1
    return cursor


def consume_line_ending(body: str, cursor: int) -> int | None:
    if cursor >= len(body) or body[cursor] not in "\r\n":
        return None
    if body[cursor] == "\r" and cursor + 1 < len(body) and body[cursor + 1] == "\n":
        return cursor + 2
    return cursor + 1


def consume_reference_definition_line_ending(
    body: str, cursor: int, tokens: tuple[tuple[str, int], ...]
) -> int | None:
    """Consume a line ending, requiring a repeat of ``tokens`` if non-empty.

    A reference definition opened inside a block quote or list item must
    repeat that container's prefix on its destination/title continuation
    lines; otherwise those lines belong to a different block and the
    destination must not be scanned across the boundary.
    """
    next_line = consume_line_ending(body, cursor)
    if next_line is None or not tokens:
        return next_line
    line_end = next_line
    while line_end < len(body) and body[line_end] not in "\r\n":
        line_end += 1
    stripped = strip_block_container_prefix(body[next_line:line_end], tokens)
    if stripped is None:
        return None
    return line_end - len(stripped)


def consume_link_destination(
    body: str, cursor: int, *, allow_empty: bool = False
) -> int | None:
    """Return the end of a CommonMark link destination, or None if invalid.

    Inline links may omit their destination entirely (``[label]()``), while
    reference definitions require a destination unless they use the explicit
    empty ``<>`` form.
    """
    start = cursor
    if cursor < len(body) and body[cursor] == "<":
        cursor += 1
        while cursor < len(body):
            if body[cursor] == "\\" and cursor + 1 < len(body):
                cursor += 2
                continue
            if body[cursor] == ">":
                return cursor + 1
            if body[cursor] in "<\r\n":
                return None
            cursor += 1
        return None

    paren_depth = 0
    while cursor < len(body):
        char = body[cursor]
        if char == "\\" and cursor + 1 < len(body):
            cursor += 2
            continue
        if ord(char) < 0x20 or char == "\x7f" or char == " ":
            break
        if char == "(":
            paren_depth += 1
        elif char == ")":
            if paren_depth == 0:
                break
            paren_depth -= 1
        cursor += 1
    if (cursor == start and not allow_empty) or paren_depth != 0:
        return None
    return cursor


def consume_link_title(body: str, cursor: int) -> int | None:
    if cursor >= len(body) or body[cursor] not in "\"'(":
        return None
    delimiter = body[cursor]
    closing_delimiter = ")" if delimiter == "(" else delimiter
    cursor += 1
    content_start = cursor
    while cursor < len(body):
        if body[cursor] == "\\" and cursor + 1 < len(body):
            cursor += 2
            continue
        if body[cursor] == closing_delimiter:
            if BLANK_LINE_RE.search(body[content_start:cursor]):
                return None
            return cursor + 1
        cursor += 1
    return None


def _parse_reference_definitions_ignoring_paragraphs(
    body: str,
) -> list[ReferenceDefinition]:
    """Parse every syntactically valid reference definition candidate.

    This does not yet enforce CommonMark's rule that a reference definition
    cannot interrupt an open paragraph; iter_reference_definitions() applies
    that filter afterwards, using these results to know where each candidate
    ends.
    """
    definitions: list[ReferenceDefinition] = []
    for (
        definition_start,
        after_colon,
        tokens,
        label,
    ) in find_reference_definition_openers(body):
        cursor = consume_spaces_and_tabs(body, after_colon)
        next_line = consume_reference_definition_line_ending(body, cursor, tokens)
        if next_line is not None:
            cursor = consume_spaces_and_tabs(body, next_line)

        target_start = cursor
        target_end = consume_link_destination(body, cursor)
        if target_end is None:
            continue

        same_line_end = consume_spaces_and_tabs(body, target_end)
        title_start = same_line_end
        title_end = (
            consume_link_title(body, title_start)
            if same_line_end > target_end
            else None
        )

        if title_end is None:
            next_line = consume_reference_definition_line_ending(
                body, same_line_end, tokens
            )
            if next_line is not None:
                candidate = consume_spaces_and_tabs(body, next_line)
                title_start = candidate
                title_end = consume_link_title(body, candidate)

        if title_end is not None:
            end = consume_spaces_and_tabs(body, title_end)
            if end < len(body) and body[end] not in "\r\n":
                continue
        else:
            end = same_line_end
            if end < len(body) and body[end] not in "\r\n":
                continue

        definitions.append(
            ReferenceDefinition(
                start=definition_start,
                end=end,
                prefix=body[definition_start:target_start],
                raw_target=body[target_start:target_end],
                suffix=body[target_end:end],
                label=label,
            )
        )
    return definitions


def iter_reference_definitions(body: str) -> list[ReferenceDefinition]:
    """Parse CommonMark link reference definitions used by the adapter.

    The destination may start on the line after the label/colon, and an
    optional title may start on the line after the destination. Exact source
    whitespace is retained in prefix/suffix so rewriting changes only the
    destination.

    A candidate is discarded when it would interrupt an already open
    paragraph, which CommonMark does not permit for reference definitions
    (unlike some other block types). Acceptance and paragraph-interruption
    eligibility are computed in one ordered, line-by-line pass so that only
    an *accepted* definition can reset the paragraph state a later candidate
    is checked against; a rejected candidate is left in place as ordinary
    paragraph text like the rest of CommonMark's grammar requires. The same
    pass also tracks other leaf blocks (fenced/indented code, HTML blocks,
    setext headings) that close without a blank line, reusing
    find_code_block_spans()/find_html_block_spans() so this doesn't
    re-derive a third, possibly-diverging copy of that container/block
    state machine.
    """
    candidates = _parse_reference_definitions_ignoring_paragraphs(body)

    lines = body.splitlines(keepends=True)
    n = len(lines)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    def line_index_of(char_offset: int) -> int:
        return bisect.bisect_right(offsets, char_offset) - 1

    candidates_by_start_line = {
        line_index_of(definition.start): definition for definition in candidates
    }

    code_spans = find_code_spans(body)
    leaf_block_spans = merge_spans(
        find_code_block_spans(body) + find_html_block_spans(body, code_spans)
    )
    leaf_block_end_line_by_start_line = {
        line_index_of(span_start): line_index_of(span_end - 1)
        for span_start, span_end in leaf_block_spans
    }

    accepted: list[ReferenceDefinition] = []
    active_list_tokens: tuple[tuple[str, int], ...] = ()
    prev_container_tokens: tuple[tuple[str, int], ...] = ()
    prev_blank = True
    paragraph_open = False
    i = 0
    while i < n:
        text = lines[i].rstrip("\r\n")
        container_tokens, content_start = parse_block_container_prefix(text)
        container_content = text[content_start:]
        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        line_starts_list = any(kind == "list" for kind, _ in container_tokens)

        if active_list_tokens and active_content is None:
            if line_starts_list:
                active_list_tokens = container_tokens
            elif container_content.strip() != "" and prev_blank:
                active_list_tokens = ()
        elif active_list_tokens and active_content is not None:
            nested_tokens, _ = parse_block_container_prefix(active_content)
            if any(kind == "list" for kind, _ in nested_tokens):
                active_list_tokens += nested_tokens
        elif line_starts_list:
            active_list_tokens = container_tokens

        active_content = (
            strip_block_container_prefix(text, active_list_tokens)
            if active_list_tokens
            else None
        )
        effective_tokens = (
            active_list_tokens if active_content is not None else container_tokens
        )
        effective_content = (
            active_content if active_content is not None else container_content
        )

        if effective_tokens != prev_container_tokens:
            paragraph_open = False

        definition = candidates_by_start_line.get(i)
        if definition is not None and not paragraph_open:
            accepted.append(definition)
            paragraph_open = False
            prev_blank = False
            prev_container_tokens = effective_tokens
            i = line_index_of(definition.end) + 1
            continue

        leaf_block_end_line = leaf_block_end_line_by_start_line.get(i)
        if leaf_block_end_line is not None:
            paragraph_open = False
            prev_blank = False
            prev_container_tokens = effective_tokens
            i = leaf_block_end_line + 1
            continue

        stripped_content = effective_content.strip()
        if stripped_content == "":
            paragraph_open = False
        elif ATX_HEADING_RE.match(effective_content) or THEMATIC_BREAK_RE.match(
            effective_content
        ):
            paragraph_open = False
        elif paragraph_open and SETEXT_UNDERLINE_RE.match(effective_content):
            paragraph_open = False
        else:
            paragraph_open = True
        prev_blank = stripped_content == ""
        prev_container_tokens = effective_tokens
        i += 1

    return accepted


def iter_inline_links(
    body: str,
    excluded_spans: list[tuple[int, int]] | None = None,
) -> list[InlineLink]:
    """Parse inline link destinations and titles needed for safe rewriting.

    find_inline_link_openers() does the full CommonMark bracket-matching and
    destination/title parsing; this only exists as the stable name the rest
    of the adapter calls.
    """
    return find_inline_link_openers(body, excluded_spans)


def normalize_reference_label(label: str) -> str:
    """CommonMark label matching: Unicode case fold plus whitespace
    collapse/strip; an empty result matches nothing."""
    return " ".join(label.split()).casefold()


def first_definition_wins(
    reference_definitions: list[ReferenceDefinition],
) -> dict[str, ReferenceDefinition]:
    """Map each normalized label to its first definition, per CommonMark's
    rule that an earlier definition shadows any later one with the same
    label."""
    by_label: dict[str, ReferenceDefinition] = {}
    for definition in reference_definitions:
        normalized = normalize_reference_label(definition.label)
        if normalized and normalized not in by_label:
            by_label[normalized] = definition
    return by_label


def find_used_reference_labels(
    body: str,
    reference_definitions: list[ReferenceDefinition],
    excluded_spans: list[tuple[int, int]],
) -> set[str]:
    """Collect labels used by actual CommonMark reference links/images.

    The bracket stack accepts balanced brackets in link text, gives inline
    links precedence over shortcut references, and applies the same
    link/image deactivation rules as find_inline_link_openers(). Brackets
    inside code spans, URI autolinks, raw HTML, or reference definitions do
    not participate in matching.
    """
    definitions_by_label = first_definition_wins(reference_definitions)
    ignored_spans = merge_spans(
        find_bracket_precedence_spans(body, excluded_spans)
        + [(definition.start, definition.end) for definition in reference_definitions]
    )

    def consume_explicit_label(start: int) -> tuple[str, int] | None:
        if start >= len(body) or body[start] != "[":
            return None
        cursor = start + 1
        characters = 0
        line_endings = 0
        while cursor < len(body):
            char = body[cursor]
            if (
                char == "\\"
                and cursor + 1 < len(body)
                and body[cursor + 1] in ASCII_PUNCTUATION
            ):
                characters += 1
                cursor += 2
                continue
            if char == "[":
                return None
            if char == "]":
                return body[start + 1 : cursor], cursor + 1
            line_end = consume_line_ending(body, cursor)
            if line_end is not None:
                line_endings += 1
                if line_endings > 1:
                    return None
                characters += 1
                cursor = line_end
                continue
            characters += 1
            if characters > 999:
                return None
            cursor += 1
        return None

    def shortcut_label(text: str) -> str | None:
        cursor = 0
        characters = 0
        line_endings = 0
        while cursor < len(text):
            char = text[cursor]
            if (
                char == "\\"
                and cursor + 1 < len(text)
                and text[cursor + 1] in ASCII_PUNCTUATION
            ):
                characters += 1
                cursor += 2
                continue
            if char in "[]":
                return None
            line_end = consume_line_ending(text, cursor)
            if line_end is not None:
                line_endings += 1
                if line_endings > 1:
                    return None
                characters += 1
                cursor = line_end
                continue
            characters += 1
            if characters > 999:
                return None
            cursor += 1
        return text

    def record_reference(
        raw_label: str, entry: dict[str, Any], stack: list[dict[str, Any]]
    ) -> bool:
        normalized = normalize_reference_label(raw_label)
        if normalized not in definitions_by_label:
            return False
        used.add(normalized)
        if not entry["is_image"]:
            for older in stack:
                older["active"] = False
        return True

    used: set[str] = set()
    stack: list[dict[str, Any]] = []
    ignored_index = 0
    cursor = 0
    while cursor < len(body):
        while (
            ignored_index < len(ignored_spans)
            and ignored_spans[ignored_index][1] <= cursor
        ):
            ignored_index += 1
        if (
            ignored_index < len(ignored_spans)
            and ignored_spans[ignored_index][0] <= cursor
        ):
            span_end = ignored_spans[ignored_index][1]
            if BLANK_LINE_RE.search(body, cursor, span_end):
                for entry in stack:
                    entry["active"] = False
            cursor = span_end
            continue

        char = body[cursor]
        if (
            char == "\\"
            and cursor + 1 < len(body)
            and body[cursor + 1] not in "\r\n"
        ):
            cursor += 2
            continue
        if char in "\r\n":
            if BLANK_LINE_RE.match(body, cursor):
                for entry in stack:
                    entry["active"] = False
            next_line = consume_line_ending(body, cursor)
            assert next_line is not None
            cursor = next_line
            continue
        if char == "[":
            image_opener = is_image_opener(body, cursor)
            stack.append(
                {
                    "opener": cursor,
                    "is_image": image_opener,
                    "active": True,
                }
            )
            cursor += 1
            continue
        if char != "]" or not stack:
            cursor += 1
            continue

        entry = stack.pop()
        if not entry["active"]:
            cursor += 1
            continue

        if cursor + 1 < len(body) and body[cursor + 1] == "(":
            inline = parse_inline_link_tail(
                body,
                entry["opener"] - (1 if entry["is_image"] else 0),
                cursor + 2,
            )
            if inline is not None:
                if not entry["is_image"]:
                    for older in stack:
                        older["active"] = False
                cursor = inline.end
                continue

        explicit = consume_explicit_label(cursor + 1)
        text = body[entry["opener"] + 1 : cursor]
        if explicit is not None:
            explicit_label, explicit_end = explicit
            label = text if explicit_label == "" else explicit_label
            if record_reference(label, entry, stack):
                cursor = explicit_end
                continue
            # Any syntactically valid explicit label suppresses shortcut
            # fallback, even when that label has no matching definition.
            cursor = explicit_end
            continue

        label = shortcut_label(text)
        if label is not None:
            record_reference(label, entry, stack)
        cursor += 1
    return used


def link_syntax_excluded(
    link: InlineLink, excluded_spans: list[tuple[int, int]]
) -> bool:
    """A link is only fake -- not real CommonMark link syntax -- when one of
    its *delimiters* (the opening bracket, "](", the destination, or the
    closing ")") sits inside a code span or raw HTML span. Content elsewhere
    in the label may legitimately overlap a code span or raw HTML (e.g.
    ``[see `child`](child.md)``) without invalidating the link, so this
    deliberately does not test the whole label-to-destination range.
    """
    open_bracket_end = link.start + (2 if link.prefix.startswith("!") else 1)
    delimiter_spans = (
        (link.start, open_bracket_end),
        (link.target_start - 2, link.target_start),
        (link.target_start, link.target_end),
        (link.end - 1, link.end),
    )
    return any(
        start < span_end and end > span_start
        for start, end in delimiter_spans
        for span_start, span_end in excluded_spans
    )


def iter_link_targets(body: str) -> list[str]:
    """Yield raw (unwrapped) destinations for inline links and reference-
    style link definitions, skipping code and raw HTML regions. Shared by the
    adapter's link rewriter and the validator's link/orphan checks so both
    inspect the exact same Markdown link forms.
    """
    excluded_spans = find_link_exclusion_spans(body)

    def excluded(start: int, end: int) -> bool:
        return any(
            start < span_end and end > span_start
            for span_start, span_end in excluded_spans
        )

    reference_definitions = iter_reference_definitions(body)

    def in_reference_definition(start: int, end: int) -> bool:
        return any(
            start < definition.end and end > definition.start
            for definition in reference_definitions
        )

    inline_links = iter_inline_links(body, excluded_spans)
    targets = [
        unwrap_angle_brackets(link.raw_target)
        for link in inline_links
        if not link_syntax_excluded(link, excluded_spans)
        and not in_reference_definition(link.start, link.end)
    ]
    used_labels = find_used_reference_labels(
        body, reference_definitions, excluded_spans
    )
    targets.extend(
        unwrap_angle_brackets(definition.raw_target)
        for definition in first_definition_wins(reference_definitions).values()
        if normalize_reference_label(definition.label) in used_labels
        and not excluded(definition.start, definition.end)
    )
    return targets


def rewrite_markdown_links(
    source_relative_path: PurePosixPath,
    body: str,
    okf_paths: set[PurePosixPath],
) -> str:
    excluded_spans = find_link_exclusion_spans(body)

    def excluded(start: int, end: int) -> bool:
        return any(
            start < span_end and end > span_start
            for span_start, span_end in excluded_spans
        )

    reference_definitions = iter_reference_definitions(body)

    def in_reference_definition(start: int, end: int) -> bool:
        return any(
            start < definition.end and end > definition.start
            for definition in reference_definitions
        )

    def rewritten_replacement(
        raw_target: str, build: Callable[[str], str]
    ) -> str | None:
        target = unwrap_angle_brackets(raw_target)
        rewritten = rewrite_link_target(source_relative_path, target, okf_paths)
        if rewritten == target:
            return None
        bracketed = raw_target.startswith("<") and raw_target.endswith(">")
        return build(f"<{rewritten}>" if bracketed else rewritten)

    replacements: list[tuple[int, int, str]] = []

    for link in iter_inline_links(body, excluded_spans):
        if link_syntax_excluded(link, excluded_spans) or in_reference_definition(
            link.start, link.end
        ):
            continue
        replacement = rewritten_replacement(
            link.raw_target,
            lambda new_target: f"{link.prefix}{new_target}{link.suffix}",
        )
        if replacement is not None:
            replacements.append((link.start, link.end, replacement))

    for definition in reference_definitions:
        if excluded(definition.start, definition.end):
            continue
        replacement = rewritten_replacement(
            definition.raw_target,
            lambda new_target: (
                f"{definition.prefix}{new_target}{definition.suffix}"
            ),
        )
        if replacement is not None:
            replacements.append((definition.start, definition.end, replacement))

    replacements.sort(key=lambda item: item[0])
    segments: list[str] = []
    cursor = 0
    for start, end, replacement in replacements:
        segments.append(body[cursor:start])
        segments.append(replacement)
        cursor = end
    segments.append(body[cursor:])
    return "".join(segments)


def rewrite_link_target(
    source_relative_path: PurePosixPath,
    target: str,
    okf_paths: set[PurePosixPath],
) -> str:
    if is_external_or_special_link(target):
        return target

    raw_link_path, suffix = split_link_suffix(target)
    link_path = decode_commonmark_link_path(raw_link_path)
    if not link_path.endswith(".md"):
        return target

    if link_path.startswith("/"):
        resolved = normalize_posix_path(PurePosixPath(link_path.lstrip("/")))
    else:
        resolved = normalize_posix_path(source_relative_path.parent / link_path)
    if resolved not in okf_paths:
        return target

    # Re-encode the resolved path segments (preserving "/") before appending
    # the query/fragment suffix, since a decoded space, "#", or "?" from the
    # source filename would otherwise land in the emitted Markdown as an
    # invalid or misinterpreted URL character.
    return quote(public_url_for_okf_path(resolved), safe="/") + suffix


def decode_commonmark_link_path(raw_path: str) -> str:
    """Decode CommonMark escapes/references, then URI percent encoding.

    This intentionally operates only on the path component selected by
    split_link_suffix(), leaving the query and fragment payload untouched.
    """
    return unquote(decode_commonmark_link_syntax(raw_path))


def decode_commonmark_link_syntax(raw_path: str) -> str:
    """Decode CommonMark escapes and references without percent-decoding.

    A single pass is required because an escaped ampersand (``\\&amp;``) is
    literal text, not the start of a character reference.
    """
    decoded: list[str] = []
    cursor = 0
    while cursor < len(raw_path):
        char = raw_path[cursor]
        if (
            char == "\\"
            and cursor + 1 < len(raw_path)
            and raw_path[cursor + 1] in ASCII_PUNCTUATION
        ):
            decoded.append(raw_path[cursor + 1])
            cursor += 2
            continue
        if char == "&":
            match = CHARACTER_REFERENCE_RE.match(raw_path, cursor)
            if match is not None:
                reference = match.group(0)
                entity_name = reference[1:] if not reference.startswith("&#") else None
                if entity_name is None or entity_name in html.entities.html5:
                    decoded.append(html.unescape(reference))
                    cursor = match.end()
                    continue
        decoded.append(char)
        cursor += 1
    return "".join(decoded)


GENERIC_URI_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def is_external_or_special_link(target: str) -> bool:
    # Callers unwrap a `<...>`-bracketed destination before reaching this
    # check, so target never itself starts with "<" here. Check both the raw
    # spelling and decoded path so schemes written with CommonMark character
    # references/escapes are not misclassified as bundle-relative paths. Any
    # absolute URI (RFC 3986 scheme grammar, e.g. "urn:example:manual.md")
    # is external even without "//", not just the "mailto:"/"tel:" special
    # cases.
    if (
        "://" in target
        or target.startswith(("#", "mailto:", "tel:", "{{"))
        or GENERIC_URI_SCHEME_RE.match(target)
    ):
        return True
    raw_path, _ = split_link_suffix(target)
    commonmark_path = decode_commonmark_link_syntax(raw_path)
    return (
        "://" in commonmark_path
        or commonmark_path.startswith(("mailto:", "tel:", "{{"))
        or bool(GENERIC_URI_SCHEME_RE.match(commonmark_path))
    )


def split_link_suffix(target: str) -> tuple[str, str]:
    """Split a raw CommonMark destination into path and query/fragment suffix.

    CommonMark decodes backslash escapes and character references before the
    destination becomes a URL, so encoded ``#`` and ``?`` characters begin
    URL suffix semantics too. Normalize only that delimiter token in the
    returned suffix; leaving its raw escape/reference spelling in a rewritten
    destination makes Hugo percent-encode or HTML-escape it as path text.
    """
    cursor = 0
    while cursor < len(target):
        if (
            target[cursor] == "\\"
            and cursor + 1 < len(target)
            and target[cursor + 1] in ASCII_PUNCTUATION
        ):
            if target[cursor + 1] in "#?":
                return target[:cursor], target[cursor + 1 :]
            cursor += 2
            continue
        if target[cursor] == "&":
            reference = CHARACTER_REFERENCE_RE.match(target, cursor)
            if reference is not None:
                value = reference.group(0)
                entity_name = value[1:] if not value.startswith("&#") else None
                if entity_name is None or entity_name in html.entities.html5:
                    decoded = html.unescape(value)
                    if decoded in "#?":
                        return (
                            target[:cursor],
                            decoded + target[reference.end() :],
                        )
                    cursor = reference.end()
                    continue
        if target[cursor] in "#?":
            return target[:cursor], target[cursor:]
        cursor += 1
    return target, ""


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
    for name in comparison.common_funny:
        differences.append(f"type differs or cannot compare: {name}")
    for name in comparison.funny_files:
        differences.append(f"cannot compare content: {name}")
    for name, subcomparison in comparison.subdirs.items():
        for difference in collect_directory_differences(subcomparison):
            differences.append(f"{name}/{difference}")
    return differences


if __name__ == "__main__":
    raise SystemExit(main())
