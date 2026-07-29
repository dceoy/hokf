#!/usr/bin/env python3
"""Generate disposable Hugo content from a canonical OKF v0.2 bundle."""

from __future__ import annotations

import argparse
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
# 3 spaces. Labels, destinations, and titles are parsed structurally below.
REFERENCE_DEFINITION_CANDIDATE_RE = re.compile(r"^[ \t]{0,3}\[", re.MULTILINE)
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


@dataclass(frozen=True)
class InlineLink:
    start: int
    end: int
    prefix: str
    raw_target: str
    suffix: str


@dataclass(frozen=True)
class ReferenceDefinition:
    start: int
    end: int
    prefix: str
    raw_target: str
    suffix: str


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


def find_code_spans(body: str) -> list[tuple[int, int]]:
    """Return merged (start, end) character spans of Markdown code regions.

    Covers fenced code blocks (including fences nested in block quotes and
    list items), indented code blocks, and inline code spans delimited by
    equal-length backtick runs (including multiline spans), so link rewriting
    can skip all of them. Active list-container indentation is retained across
    blank lines so ordinary list continuations remain prose while content
    indented four additional columns is recognized as a code block.
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

    block_spans = list(spans)

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

    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


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


def find_inline_link_openers(body: str) -> list[tuple[int, int]]:
    """Locate `[`/`![` openers whose balanced CommonMark label is followed by
    "(".

    Nested brackets (`[see [child]](child.md)`) and backslash-escaped
    delimiters (`[child \\]](child.md)`) inside the label are honored, since a
    regular expression cannot track bracket depth. Soft line endings are
    allowed inside labels, but blank lines terminate the candidate.

    Returns (label_start, destination_start) pairs, where label_start is the
    index of the optional "!" or the "[", and destination_start is the index
    just after the "(" that must immediately follow the closing "]".
    """
    openers: list[tuple[int, int]] = []
    length = len(body)
    index = 0
    while index < length:
        char = body[index]
        if char == "\\" and index + 1 < length:
            index += 2
            continue
        if char != "[":
            index += 1
            continue

        label_start = index - 1 if index > 0 and body[index - 1] == "!" else index
        depth = 0
        cursor = index
        closed_at = -1
        while cursor < length:
            inner = body[cursor]
            if inner == "\\" and cursor + 1 < length:
                cursor += 2
                continue
            if inner in "\r\n":
                if BLANK_LINE_RE.match(body, cursor):
                    break
                next_line = consume_line_ending(body, cursor)
                assert next_line is not None
                cursor = next_line
                continue
            if inner == "[":
                depth += 1
            elif inner == "]":
                depth -= 1
                if depth == 0:
                    closed_at = cursor
                    break
            cursor += 1

        if closed_at != -1 and closed_at + 1 < length and body[closed_at + 1] == "(":
            openers.append((label_start, closed_at + 2))
            index = closed_at + 2
            continue
        index += 1
    return openers


def find_reference_definition_openers(body: str) -> list[tuple[int, int]]:
    """Locate CommonMark reference labels and return (start, after-colon).

    Reference labels may contain backslash-escaped punctuation and one line
    ending, but not an unescaped opening bracket or an empty/whitespace-only
    label.
    """
    openers: list[tuple[int, int]] = []
    for match in REFERENCE_DEFINITION_CANDIDATE_RE.finditer(body):
        cursor = match.end()
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
                    openers.append((match.start(), cursor + 2))
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


def consume_link_destination(body: str, cursor: int) -> int | None:
    """Return the end of a CommonMark link destination, or None if invalid."""
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
    if cursor == start or paren_depth != 0:
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


def iter_reference_definitions(body: str) -> list[ReferenceDefinition]:
    """Parse CommonMark link reference definitions used by the adapter.

    The destination may start on the line after the label/colon, and an
    optional title may start on the line after the destination. Exact source
    whitespace is retained in prefix/suffix so rewriting changes only the
    destination.
    """
    definitions: list[ReferenceDefinition] = []
    for definition_start, after_colon in find_reference_definition_openers(body):
        cursor = consume_spaces_and_tabs(body, after_colon)
        next_line = consume_line_ending(body, cursor)
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
            next_line = consume_line_ending(body, same_line_end)
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
            )
        )
    return definitions


def iter_inline_links(body: str) -> list[InlineLink]:
    """Parse inline link destinations and titles needed for safe rewriting.

    Label boundaries come from find_inline_link_openers(); the destination
    and optional title are scanned character by character so balanced
    parentheses, escaped delimiters, multiline titles, and the spaces, tabs,
    or single line ending permitted between components are handled
    consistently with CommonMark.
    """
    links: list[InlineLink] = []
    for label_start, cursor in find_inline_link_openers(body):
        cursor = consume_link_whitespace(body, cursor)
        target_start = cursor
        target_end = consume_link_destination(body, cursor)
        if target_end is None:
            continue
        cursor = target_end
        whitespace_start = cursor
        cursor = consume_link_whitespace(body, cursor)

        if cursor > whitespace_start and cursor < len(body) and body[cursor] != ")":
            title_end = consume_link_title(body, cursor)
            if title_end is None:
                continue
            cursor = title_end
            cursor = consume_link_whitespace(body, cursor)

        if cursor >= len(body) or body[cursor] != ")":
            continue

        links.append(
            InlineLink(
                start=label_start,
                end=cursor + 1,
                prefix=body[label_start:target_start],
                raw_target=body[target_start:target_end],
                suffix=body[target_end : cursor + 1],
            )
        )
    return links


def iter_link_targets(body: str) -> list[str]:
    """Yield raw (unwrapped) destinations for inline links and reference-
    style link definitions, skipping code regions. Shared by the adapter's
    link rewriter and the validator's link/orphan checks so both inspect the
    exact same Markdown link forms.
    """
    code_spans = find_code_spans(body)

    def in_code(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in code_spans)

    reference_definitions = iter_reference_definitions(body)

    def in_reference_definition(start: int, end: int) -> bool:
        return any(
            start < definition.end and end > definition.start
            for definition in reference_definitions
        )

    targets = [
        unwrap_angle_brackets(link.raw_target)
        for link in iter_inline_links(body)
        if not in_code(link.start, link.end)
        and not in_reference_definition(link.start, link.end)
    ]
    targets.extend(
        unwrap_angle_brackets(definition.raw_target)
        for definition in reference_definitions
        if not in_code(definition.start, definition.end)
    )
    return targets


def rewrite_markdown_links(
    source_relative_path: PurePosixPath,
    body: str,
    okf_paths: set[PurePosixPath],
) -> str:
    code_spans = find_code_spans(body)

    def in_code(start: int, end: int) -> bool:
        return any(start < c_end and end > c_start for c_start, c_end in code_spans)

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

    for link in iter_inline_links(body):
        if in_code(link.start, link.end) or in_reference_definition(
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
        if in_code(definition.start, definition.end):
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
    return unquote("".join(decoded))


def is_external_or_special_link(target: str) -> bool:
    # Callers unwrap a `<...>`-bracketed destination before reaching this
    # check, so target never itself starts with "<" here. Check both the raw
    # spelling and decoded path so schemes written with CommonMark character
    # references/escapes are not misclassified as bundle-relative paths.
    if "://" in target or target.startswith(("#", "mailto:", "tel:", "{{")):
        return True
    raw_path, _ = split_link_suffix(target)
    decoded_path = decode_commonmark_link_path(raw_path)
    return "://" in decoded_path or decoded_path.startswith(("mailto:", "tel:", "{{"))


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
