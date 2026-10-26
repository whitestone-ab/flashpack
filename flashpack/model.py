"""Core data model for the frozen v1 snapshot format."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Status(str, Enum):
    """D6: the only statuses allowed for files that enter the file list."""

    FULL = "full"
    SKELETON = "skeleton"
    TRUNCATED = "truncated"
    OMITTED = "omitted"


@dataclass
class File:
    """A file that has entered the file list (D17).

    ``text`` is the original decoded text (possibly a prefix for oversized
    files). ``content`` is the compressed/truncated text that will be emitted.
    ``tokens`` is the token estimate for the current content candidate and is
    kept even when the budget turns the file into ``omitted`` so the file-list
    can still report a meaningful size.
    """

    path: str
    abs_path: Path
    lang: str
    lines: int
    size: int
    status: Status = Status.FULL
    text: str = ""
    content: str = ""
    tokens: int = 0
    refs: int = 0
    depends_on: list[str] = field(default_factory=list)
    fid: str = ""
    reason: str = ""
    encoding: str = "utf-8"
    truncated_from: int | None = None

    @property
    def in_content(self) -> bool:
        return self.status is not Status.OMITTED


@dataclass(frozen=True)
class Skipped:
    """A file intentionally excluded from the file list (D17)."""

    path: str
    reason: str


@dataclass
class Snapshot:
    root: Path
    project: str
    files: list[File] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    files_total: int = 0
    ignored_dirs: int = 0
    budget: int = 32_000
    total_lines: int = 0
    total_bytes: int = 0
    estimated_tokens: int = 0
    warnings: list[str] = field(default_factory=list)
    js_fallback: bool = False
    list_mode: bool = False
    budget_low: bool = False
    budget_exceeded: bool = False
    budget_omitted_count: int = 0
    token_name: str = "unknown"

    def sorted_files(self) -> list[File]:
        """File-list order is exactly FID order (D5/D10)."""
        return sorted(self.files, key=lambda f: f.fid)

    def skipped_sorted(self) -> list[Skipped]:
        return sorted(self.skipped, key=lambda s: s.path)
