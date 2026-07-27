#!/usr/bin/env python3
"""Shared parsing helpers for Open Knowledge Format Markdown."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


FRONT_MATTER_DELIMITER = "---"


class FrontMatterError(ValueError):
    """Raised when a YAML front matter block cannot be parsed."""


@dataclass(frozen=True)
class OkfDocument:
    source_path: Path
    relative_path: PurePosixPath
    metadata: dict[str, Any] | None
    body: str
    has_front_matter: bool

    @property
    def is_index(self) -> bool:
        return self.relative_path.name == "index.md"

    @property
    def is_log(self) -> bool:
        return self.relative_path.name == "log.md"

    @property
    def is_reserved(self) -> bool:
        return self.is_index or self.is_log


def read_document(source_path: Path, root: Path) -> OkfDocument:
    relative_path = PurePosixPath(source_path.relative_to(root).as_posix())
    if source_path.is_symlink():
        raise FrontMatterError("symbolic-link Markdown documents are not supported")
    try:
        resolved_path = source_path.resolve(strict=True)
    except OSError as error:
        raise FrontMatterError(f"cannot resolve Markdown document: {error}") from error
    if not resolved_path.is_relative_to(root.resolve()):
        raise FrontMatterError("Markdown document resolves outside the OKF bundle")
    metadata, body, has_front_matter = split_front_matter(
        source_path.read_text(encoding="utf-8")
    )
    return OkfDocument(
        source_path=source_path,
        relative_path=relative_path,
        metadata=metadata,
        body=body,
        has_front_matter=has_front_matter,
    )


def read_documents(root: Path) -> list[OkfDocument]:
    return [
        read_document(source_path, root)
        for source_path in sorted(root.rglob("*.md"))
    ]


def split_front_matter(markdown: str) -> tuple[dict[str, Any] | None, str, bool]:
    lines = markdown.splitlines(keepends=True)
    if not lines or lines[0].strip() != FRONT_MATTER_DELIMITER:
        return None, markdown, False

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() in {FRONT_MATTER_DELIMITER, "..."}
        ),
        None,
    )
    if closing_index is None:
        raise FrontMatterError("front matter is missing a closing delimiter")

    source = "".join(lines[1:closing_index])
    try:
        loaded = yaml.safe_load(source)
    except yaml.YAMLError as error:
        problem = getattr(error, "problem", None) or str(error).splitlines()[0]
        raise FrontMatterError(f"invalid YAML front matter: {problem}") from error

    if loaded is None:
        metadata: dict[str, Any] = {}
    elif isinstance(loaded, dict):
        if not all(isinstance(key, str) for key in loaded):
            raise FrontMatterError("front matter keys must be strings")
        metadata = loaded
    else:
        raise FrontMatterError("front matter must be a YAML mapping")

    return metadata, "".join(lines[closing_index + 1 :]), True


class _NoAliasSafeDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def dump_front_matter(metadata: dict[str, Any]) -> str:
    dumped = yaml.dump(
        metadata,
        Dumper=_NoAliasSafeDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).rstrip()
    return f"{FRONT_MATTER_DELIMITER}\n{dumped}\n{FRONT_MATTER_DELIMITER}\n"
