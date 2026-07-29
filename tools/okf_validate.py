#!/usr/bin/env python3
"""Validate OKF v0.2 conformance and report advisory quality findings."""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

try:
    from tools.okf_common import FrontMatterError, OkfDocument, read_document
    from tools.okf_hugo_adapter import (
        decode_commonmark_link_path,
        find_code_block_spans,
        find_html_block_spans,
        is_external_or_special_link,
        iter_link_targets,
        merge_spans,
        normalize_posix_path,
        split_link_suffix,
    )
except ModuleNotFoundError:
    from okf_common import (  # type: ignore[no-redef]
        FrontMatterError,
        OkfDocument,
        read_document,
    )
    from okf_hugo_adapter import (  # type: ignore[no-redef]
        decode_commonmark_link_path,
        find_code_block_spans,
        find_html_block_spans,
        is_external_or_special_link,
        iter_link_targets,
        merge_spans,
        normalize_posix_path,
        split_link_suffix,
    )


ATX_HEADING_LINE_RE = re.compile(
    r"^[ \t]{0,3}(#{1,6})(?:[ \t]+(.*?)[ \t]*|[ \t]*)$"
)
ACTOR_RE = re.compile(r"^(?:human:[^\s:]+|process:[^\s:]+|[^\s/]+/[^\s/]+)$")
TAG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
STATUS_VALUES = {"draft", "stable", "deprecated"}


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    severity: str
    subject: str
    message: str

    def render(self) -> str:
        return f"{self.severity} {self.path} [{self.subject}]: {self.message}"


@dataclass(frozen=True)
class MarkdownHeading:
    level: int
    text: str
    line_start: int


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    src = args.src.resolve()
    if not src.is_dir():
        print(f"ERROR {src} [bundle]: source directory does not exist", file=sys.stderr)
        return 2

    findings = validate_bundle(src)
    for finding in findings:
        stream = sys.stderr if finding.severity == "ERROR" else sys.stdout
        print(finding.render(), file=stream)

    errors = sum(finding.severity == "ERROR" for finding in findings)
    warnings = sum(finding.severity == "WARNING" for finding in findings)
    print(f"validation complete: {errors} error(s), {warnings} warning(s)")
    return int(errors > 0 or (args.warnings_as_errors and warnings > 0))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an Open Knowledge Format v0.2 bundle."
    )
    parser.add_argument("--src", type=Path, default=Path("okf"))
    parser.add_argument(
        "--warnings-as-errors",
        action="store_true",
        help="return a non-zero status when advisory findings are present",
    )
    return parser.parse_args(argv)


def validate_bundle(root: Path, *, today: date | None = None) -> list[Finding]:
    current_date = today or date.today()
    documents: list[OkfDocument] = []
    findings: list[Finding] = []

    for source_path in sorted(root.rglob("*.md")):
        relative_path = source_path.relative_to(root).as_posix()
        try:
            documents.append(read_document(source_path, root))
        except (FrontMatterError, UnicodeDecodeError) as error:
            findings.append(
                Finding(
                    relative_path,
                    "ERROR",
                    "frontmatter",
                    f"{error}; fix the YAML front matter block",
                )
            )

    paths = {document.relative_path for document in documents}
    indexed_concepts: set[PurePosixPath] = set()

    for document in documents:
        if document.is_index:
            findings.extend(validate_index(document))
            indexed_concepts.update(resolve_document_links(document, paths))
        elif document.is_log:
            findings.extend(validate_log(document))
        else:
            findings.extend(validate_concept(document, current_date))
        findings.extend(validate_links(document, paths))

    concepts = [document for document in documents if not document.is_reserved]
    for document in concepts:
        if document.relative_path not in indexed_concepts:
            findings.append(
                warning(
                    document,
                    "index",
                    "concept is not referenced from an available index; "
                    "add a concise index entry",
                )
            )

    findings.extend(find_tag_consistency_warnings(concepts))
    findings.extend(find_duplicate_warnings(concepts))
    return sorted(set(findings))


def validate_index(document: OkfDocument) -> list[Finding]:
    findings: list[Finding] = []
    is_root = document.relative_path == PurePosixPath("index.md")

    if not is_root:
        if document.has_front_matter:
            findings.append(
                error(
                    document,
                    "frontmatter",
                    "front matter is only permitted in the bundle-root index.md; "
                    "remove it",
                )
            )
    else:
        metadata = document.metadata or {}
        extra_keys = sorted(set(metadata) - {"okf_version"})
        if extra_keys:
            findings.append(
                error(
                    document,
                    "frontmatter",
                    "root index front matter may contain only okf_version; "
                    f"remove {', '.join(extra_keys)}",
                )
            )
        if "okf_version" in metadata and metadata["okf_version"] != "0.2":
            findings.append(
                error(
                    document,
                    "okf_version",
                    'declared version must be the string "0.2"',
                )
            )

    headings = find_markdown_headings(document.body)
    if not any(heading.level == 1 and heading.text for heading in headings):
        findings.append(
            error(
                document,
                "body",
                "index must contain at least one level-one section heading",
            )
        )
    return findings


def validate_log(document: OkfDocument) -> list[Finding]:
    findings: list[Finding] = []
    if document.has_front_matter:
        findings.append(
            error(
                document,
                "frontmatter",
                "reserved log.md files must not use front matter",
            )
        )
    headings = find_markdown_headings(document.body)
    first_content_line = first_nonblank_line_start(document.body)
    if not any(
        heading.line_start == first_content_line
        and heading.level == 1
        and heading.text
        for heading in headings
    ):
        findings.append(
            error(document, "body", "log must start with a level-one title")
        )

    level_two_headings = [
        heading.text for heading in headings if heading.level == 2
    ]
    if not level_two_headings:
        findings.append(
            error(
                document,
                "date headings",
                "log must contain at least one ISO YYYY-MM-DD level-two heading",
            )
        )
        return findings

    parsed_dates: list[date] = []
    for heading in level_two_headings:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", heading):
            findings.append(
                error(
                    document,
                    f"heading:{heading}",
                    "level-two log headings must use ISO YYYY-MM-DD dates",
                )
            )
            continue
        try:
            parsed_dates.append(date.fromisoformat(heading))
        except ValueError:
            findings.append(
                error(document, f"heading:{heading}", "log heading is not a valid date")
            )

    if parsed_dates != sorted(parsed_dates, reverse=True):
        findings.append(
            error(
                document,
                "date headings",
                "log date headings must be ordered newest first",
            )
        )
    return findings


def find_markdown_headings(body: str) -> list[MarkdownHeading]:
    """Return ATX headings outside CommonMark code and raw-HTML blocks."""
    code_spans = find_code_block_spans(body)
    excluded_spans = merge_spans(
        code_spans + find_html_block_spans(body, code_spans)
    )
    headings: list[MarkdownHeading] = []
    excluded_index = 0
    offset = 0
    for line in body.splitlines(keepends=True):
        while (
            excluded_index < len(excluded_spans)
            and excluded_spans[excluded_index][1] <= offset
        ):
            excluded_index += 1
        is_excluded = (
            excluded_index < len(excluded_spans)
            and excluded_spans[excluded_index][0] <= offset
        )
        if not is_excluded:
            match = ATX_HEADING_LINE_RE.fullmatch(line.rstrip("\r\n"))
            if match is not None:
                headings.append(
                    MarkdownHeading(
                        level=len(match.group(1)),
                        text=(match.group(2) or "").strip(),
                        line_start=offset,
                    )
                )
        offset += len(line)
    return headings


def first_nonblank_line_start(body: str) -> int | None:
    """Return the offset of the first line containing non-whitespace text."""
    offset = 0
    for line in body.splitlines(keepends=True):
        if line.rstrip("\r\n").strip(" \t"):
            return offset
        offset += len(line)
    return None


def validate_concept(document: OkfDocument, current_date: date) -> list[Finding]:
    if not document.has_front_matter:
        return [
            error(
                document,
                "frontmatter",
                "concept must begin with a parseable YAML front matter block",
            )
        ]

    metadata = document.metadata or {}
    findings: list[Finding] = []
    concept_type = metadata.get("type")
    if not is_nonempty_string(concept_type):
        findings.append(
            error(document, "type", "concept must declare a non-empty string type")
        )

    if "title" not in metadata or not is_nonempty_string(metadata["title"]):
        findings.append(
            warning(
                document,
                "title",
                "recommended title is missing; add a human-readable display name",
            )
        )
    if "description" not in metadata or not is_nonempty_string(
        metadata["description"]
    ):
        findings.append(
            warning(
                document,
                "description",
                "recommended description is missing; add a concise summary",
            )
        )
    if "timestamp" in metadata:
        findings.append(
            warning(
                document,
                "timestamp",
                "legacy timestamp is superseded by generated.at; "
                "migrate when updating this concept",
            )
        )

    findings.extend(validate_optional_metadata(document, metadata, current_date))
    return findings


def validate_optional_metadata(
    document: OkfDocument, metadata: dict[str, Any], current_date: date
) -> list[Finding]:
    findings: list[Finding] = []

    if "resource" in metadata and not is_nonempty_string(metadata["resource"]):
        findings.append(
            warning(document, "resource", "resource must be a non-empty string")
        )

    if "tags" in metadata:
        tags = metadata["tags"]
        if not isinstance(tags, list) or not all(
            is_nonempty_string(tag) for tag in tags
        ):
            findings.append(
                warning(
                    document, "tags", "tags must be a YAML list of non-empty strings"
                )
            )

    if "generated" in metadata:
        generated = metadata["generated"]
        if not isinstance(generated, dict):
            findings.append(
                warning(
                    document,
                    "generated",
                    "generated must be a mapping with by and at",
                )
            )
        else:
            findings.extend(validate_event(document, "generated", generated))

    if "verified" in metadata:
        verified = metadata["verified"]
        events = [verified] if isinstance(verified, dict) else verified
        if not isinstance(events, list) or not events:
            findings.append(
                warning(
                    document,
                    "verified",
                    "verified must be an event mapping or a non-empty list "
                    "of event mappings",
                )
            )
        else:
            for index, event in enumerate(events):
                if not isinstance(event, dict):
                    findings.append(
                        warning(
                            document,
                            f"verified[{index}]",
                            "verification event must be a mapping with by and at",
                        )
                    )
                else:
                    findings.extend(
                        validate_event(document, f"verified[{index}]", event)
                    )

    if "sources" in metadata:
        sources = metadata["sources"]
        if not isinstance(sources, list):
            findings.append(
                warning(document, "sources", "sources must be a YAML list of mappings")
            )
        else:
            for index, source in enumerate(sources):
                findings.extend(validate_source(document, index, source))

    if "usage_window" in metadata:
        findings.extend(
            validate_usage_window(document, "usage_window", metadata["usage_window"])
        )

    if "status" in metadata and metadata["status"] not in STATUS_VALUES:
        findings.append(
            warning(
                document,
                "status",
                "status must be one of draft, stable, or deprecated",
            )
        )

    if "stale_after" in metadata:
        stale_after = parse_iso_date(metadata["stale_after"])
        if stale_after is None:
            findings.append(
                warning(
                    document,
                    "stale_after",
                    "stale_after must be an ISO YYYY-MM-DD date",
                )
            )
        elif current_date >= stale_after:
            findings.append(
                warning(
                    document,
                    "stale_after",
                    f"concept became stale on {stale_after.isoformat()}; "
                    "review or deprecate it",
                )
            )

    findings.extend(validate_computation_fields(document, metadata))
    return findings


def validate_event(
    document: OkfDocument, subject: str, event: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    actor = event.get("by")
    if not is_nonempty_string(actor) or not ACTOR_RE.fullmatch(actor):
        findings.append(
            warning(
                document,
                f"{subject}.by",
                "actor must use human:<id>, process:<id>, or <producer>/<version>",
            )
        )
    if parse_iso_datetime(event.get("at")) is None:
        findings.append(
            warning(
                document,
                f"{subject}.at",
                "event time must be an ISO 8601 datetime",
            )
        )
    return findings


def validate_source(
    document: OkfDocument, index: int, source: Any
) -> list[Finding]:
    subject = f"sources[{index}]"
    if not isinstance(source, dict):
        return [warning(document, subject, "source entry must be a mapping")]

    findings: list[Finding] = []
    if not is_nonempty_string(source.get("resource")):
        findings.append(
            warning(document, f"{subject}.resource", "source resource is required")
        )
    for key in ("id", "title", "author"):
        if key in source and not is_nonempty_string(source[key]):
            findings.append(
                warning(
                    document, f"{subject}.{key}", f"{key} must be a non-empty string"
                )
            )
    if "usage_count" in source and (
        isinstance(source["usage_count"], bool)
        or not isinstance(source["usage_count"], int)
        or source["usage_count"] < 0
    ):
        findings.append(
            warning(
                document,
                f"{subject}.usage_count",
                "usage_count must be a non-negative integer",
            )
        )
    if "last_modified" in source and parse_iso_date(source["last_modified"]) is None:
        findings.append(
            warning(
                document,
                f"{subject}.last_modified",
                "last_modified must be an ISO YYYY-MM-DD date",
            )
        )
    if "usage_window" in source:
        findings.extend(
            validate_usage_window(
                document, f"{subject}.usage_window", source["usage_window"]
            )
        )
    return findings


def validate_usage_window(
    document: OkfDocument, subject: str, value: Any
) -> list[Finding]:
    if not isinstance(value, dict):
        return [
            warning(
                document,
                subject,
                "usage window must be a mapping with from and to dates",
            )
        ]
    findings = []
    start = parse_iso_date(value.get("from"))
    end = parse_iso_date(value.get("to"))
    if start is None:
        findings.append(
            warning(document, f"{subject}.from", "from must be an ISO YYYY-MM-DD date")
        )
    if end is None:
        findings.append(
            warning(document, f"{subject}.to", "to must be an ISO YYYY-MM-DD date")
        )
    if start is not None and end is not None and start > end:
        findings.append(
            warning(document, subject, "usage window from date must not follow to date")
        )
    return findings


def validate_computation_fields(
    document: OkfDocument, metadata: dict[str, Any]
) -> list[Finding]:
    findings: list[Finding] = []
    if metadata.get("type") == "Attested Computation" and not is_nonempty_string(
        metadata.get("runtime")
    ):
        findings.append(
            warning(
                document,
                "runtime",
                "Attested Computation concepts require a non-empty runtime",
            )
        )
    if "runtime" in metadata and not is_nonempty_string(metadata["runtime"]):
        findings.append(
            warning(document, "runtime", "runtime must be a non-empty string")
        )
    if "computation" in metadata and not is_nonempty_string(metadata["computation"]):
        findings.append(
            warning(
                document,
                "computation",
                "computation must be a non-empty path string",
            )
        )
    if "parameters" in metadata:
        parameters = metadata["parameters"]
        if not isinstance(parameters, list):
            findings.append(
                warning(document, "parameters", "parameters must be a YAML list")
            )
        else:
            for index, parameter in enumerate(parameters):
                subject = f"parameters[{index}]"
                if not isinstance(parameter, dict):
                    findings.append(
                        warning(document, subject, "parameter must be a mapping")
                    )
                    continue
                for key in ("name", "type"):
                    if not is_nonempty_string(parameter.get(key)):
                        findings.append(
                            warning(
                                document,
                                f"{subject}.{key}",
                                f"parameter {key} must be a non-empty string",
                            )
                        )
                if "required" in parameter and not isinstance(
                    parameter["required"], bool
                ):
                    findings.append(
                        warning(
                            document,
                            f"{subject}.required",
                            "parameter required must be a boolean",
                        )
                    )
    for field in ("executor", "attester"):
        if field not in metadata:
            continue
        value = metadata[field]
        if not isinstance(value, dict):
            findings.append(warning(document, field, f"{field} must be a mapping"))
            continue
        if not is_nonempty_string(value.get("resource")):
            findings.append(
                warning(
                    document,
                    f"{field}.resource",
                    f"{field} resource must be a non-empty string",
                )
            )
        if field == "executor" and "receipt" in value and (
            not isinstance(value["receipt"], list)
            or not all(is_nonempty_string(item) for item in value["receipt"])
        ):
            findings.append(
                warning(
                    document,
                    "executor.receipt",
                    "executor receipt must be a list of non-empty field names",
                )
            )
    return findings


def validate_links(
    document: OkfDocument, paths: set[PurePosixPath]
) -> list[Finding]:
    findings = []
    for target in iter_link_targets(document.body):
        if is_external_or_special_link(target):
            continue
        raw_link_path, _ = split_link_suffix(target)
        link_path = decode_commonmark_link_path(raw_link_path)
        if not link_path:
            continue
        resolved = resolve_link_path(document.relative_path, link_path)
        if resolved is not None and resolved not in paths:
            findings.append(
                warning(
                    document,
                    f"link:{target}",
                    "internal Markdown link does not resolve; "
                    "fix the path or add the target",
                )
            )
    return findings


def resolve_document_links(
    document: OkfDocument, paths: set[PurePosixPath]
) -> set[PurePosixPath]:
    resolved_paths = set()
    for target in iter_link_targets(document.body):
        if is_external_or_special_link(target):
            continue
        raw_link_path, _ = split_link_suffix(target)
        link_path = decode_commonmark_link_path(raw_link_path)
        resolved = resolve_link_path(document.relative_path, link_path)
        if resolved in paths:
            resolved_paths.add(resolved)
    return resolved_paths


def resolve_link_path(
    source_path: PurePosixPath, link_path: str
) -> PurePosixPath | None:
    if not link_path:
        return None
    if link_path.startswith("/"):
        resolved = normalize_posix_path(PurePosixPath(link_path.lstrip("/")))
    else:
        resolved = normalize_posix_path(source_path.parent / link_path)
    if link_path.endswith("/"):
        resolved /= "index.md"
    elif not link_path.endswith(".md"):
        return None
    return resolved


def find_tag_consistency_warnings(
    concepts: Iterable[OkfDocument],
) -> list[Finding]:
    findings = []
    variants: dict[str, set[str]] = {}
    owners: dict[str, list[OkfDocument]] = {}
    for document in concepts:
        tags = (document.metadata or {}).get("tags", [])
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str):
                continue
            normalized = re.sub(r"[-_\s]+", "-", tag.strip().casefold())
            variants.setdefault(normalized, set()).add(tag)
            owners.setdefault(normalized, []).append(document)
            if not TAG_RE.fullmatch(tag):
                findings.append(
                    warning(
                        document,
                        f"tag:{tag}",
                        "tag should use lowercase kebab-case for consistent discovery",
                    )
                )
    for normalized, spellings in variants.items():
        if len(spellings) > 1:
            for document in owners[normalized]:
                findings.append(
                    warning(
                        document,
                        f"tag:{normalized}",
                        f"inconsistent variants found: {', '.join(sorted(spellings))}",
                    )
                )
    return findings


def find_duplicate_warnings(concepts: Iterable[OkfDocument]) -> list[Finding]:
    grouped: dict[str, list[OkfDocument]] = {}
    for document in concepts:
        title = (document.metadata or {}).get("title")
        if not is_nonempty_string(title):
            continue
        normalized = re.sub(
            r"[^\w]+", "", unicodedata.normalize("NFKC", title).casefold()
        )
        if not normalized:
            continue
        grouped.setdefault(normalized, []).append(document)

    findings = []
    for documents in grouped.values():
        if len(documents) < 2:
            continue
        paths = ", ".join(str(document.relative_path) for document in documents)
        for document in documents:
            findings.append(
                warning(
                    document,
                    "title",
                    f"possible duplicate title across: {paths}; "
                    "consolidate or disambiguate",
                )
            )
    return findings


def parse_iso_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return None
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if "T" in value else None


def is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def error(document: OkfDocument, subject: str, message: str) -> Finding:
    return Finding(str(document.relative_path), "ERROR", subject, message)


def warning(document: OkfDocument, subject: str, message: str) -> Finding:
    return Finding(str(document.relative_path), "WARNING", subject, message)


if __name__ == "__main__":
    raise SystemExit(main())
