#!/usr/bin/env python3
"""Shared parsing helpers for Open Knowledge Format Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml


FRONT_MATTER_DELIMITER = "---"
_FRONT_MATTER_DELIMITER_RE = re.compile(r"^---[ \t]*\r?\n?\Z")


class FrontMatterError(ValueError):
    """Raised when a YAML front matter block cannot be parsed."""


class _ProducerSafeLoader(yaml.SafeLoader):
    """SafeLoader restricted to the YAML 1.2 core-schema scalar tags."""


# PyYAML's SafeLoader follows YAML 1.1, whose bool/int/timestamp resolvers
# coerce or reinterpret bare producer-defined scalars: `on`/`yes` become
# booleans, a leading-zero digit string like `0123` is read as octal (83),
# a colon-separated digit string like `12:34` is read as sexagesimal (754),
# and date-shaped strings become `date`/`datetime` objects. All of this
# corrupts producer-defined values on an adapter read/write round trip.
# Replace every implicit resolver with just the YAML 1.2 core schema's
# null/bool/int/float rules, so any scalar that does not match one of those
# exact forms is preserved as a plain string.
_ProducerSafeLoader.yaml_implicit_resolvers = {}
_ProducerSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:null",
    re.compile(r"^(?:~|null|Null|NULL|)$"),
    ["~", "n", "N", ""],
)
_ProducerSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)
_ProducerSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    re.compile(r"^(?:[-+]?(?:0|[1-9][0-9]*)|0o[0-7]+|0x[0-9a-fA-F]+)$"),
    list("-+0123456789"),
)
_ProducerSafeLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    re.compile(
        r"^(?:[-+]?(?:(?:\.[0-9]+|[0-9]+\.[0-9]*)(?:[eE][-+]?[0-9]+)?"
        r"|[0-9]+[eE][-+]?[0-9]+)"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$"
    ),
    list("-+0123456789."),
)


def _construct_core_schema_int(loader: yaml.SafeLoader, node: yaml.Node) -> int:
    # SafeConstructor.construct_yaml_int applies YAML 1.1 value semantics
    # (including octal and sexagesimal forms) regardless of which tag the
    # resolver assigned.
    # Reimplement construction for the YAML 1.2 core-schema int forms the
    # resolver above actually matches: plain decimal, and explicit 0o/0x.
    value = loader.construct_scalar(node)
    sign = -1 if value[0] == "-" else 1
    if value[0] in "+-":
        value = value[1:]
    if value.startswith("0o"):
        return sign * int(value[2:], 8)
    if value.startswith("0x"):
        return sign * int(value[2:], 16)
    return sign * int(value)


_ProducerSafeLoader.add_constructor(
    "tag:yaml.org,2002:int", _construct_core_schema_int
)


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
    if not lines or not _FRONT_MATTER_DELIMITER_RE.match(lines[0]):
        return None, markdown, False

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if _FRONT_MATTER_DELIMITER_RE.match(line)
        ),
        None,
    )
    if closing_index is None:
        raise FrontMatterError("front matter is missing a closing delimiter")

    source = "".join(lines[1:closing_index])
    try:
        loaded = yaml.load(source, Loader=_ProducerSafeLoader)
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
