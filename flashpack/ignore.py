"""Small, dependency-free gitignore/glob matcher.

Only the common gitignore subset is supported: ``*``, ``**``, ``?``,
character classes, ``!`` negation, a trailing ``/`` for directories and a
leading ``/`` for anchoring. Nested .gitignore files are intentionally not
handled in v1.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


def _translate_body(pattern: str) -> str:
    out: list[str] = []
    i = 0
    n = len(pattern)
    while i < n:
        ch = pattern[i]
        if ch == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                j = i
                while j < n and pattern[j] == "*":
                    j += 1
                if j < n and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    j += 1
                else:
                    out.append(".*")
                i = j
            else:
                out.append("[^/]*")
                i += 1
        elif ch == "?":
            out.append("[^/]")
            i += 1
        elif ch == "[":
            j = i + 1
            if j < n and pattern[j] in "!^":
                j += 1
            if j < n and pattern[j] == "]":
                j += 1
            while j < n and pattern[j] != "]":
                j += 1
            if j >= n:
                out.append(r"\[")
                i += 1
            else:
                cls = pattern[i + 1 : j]
                if cls.startswith("!"):
                    cls = "^" + cls[1:]
                out.append("[" + cls + "]")
                i = j + 1
        elif ch == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 2
        else:
            out.append(re.escape(ch))
            i += 1
    return "".join(out)


def _normalize_path(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    negated: bool
    dir_only: bool
    source: str


def _compile_ignore(pattern: str) -> _Pattern | None:
    original = pattern
    # Git ignores trailing whitespace unless escaped; this simpler parser
    # strips it, which is fine for normal project files.
    pattern = pattern.strip()
    if not pattern or pattern.startswith("#"):
        return None
    negated = False
    if pattern.startswith("\\!"):
        pattern = pattern[1:]
    elif pattern.startswith("!"):
        negated = True
        pattern = pattern[1:].strip()
        if not pattern:
            return None
    if pattern.startswith("\\#"):
        pattern = pattern[1:]

    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern.rstrip("/")
    anchored = pattern.startswith("/") or "/" in pattern
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if not pattern:
        return None

    body = _translate_body(pattern)
    prefix = "^" if anchored else r"(?:^|/)"
    # The lookahead stops "foo" from matching "foobar.py" while still
    # allowing a directory pattern to match everything below it.
    regex = re.compile(prefix + body + r"(?=/|$)")
    return _Pattern(regex=regex, negated=negated, dir_only=dir_only, source=original)


class IgnoreMatcher:
    def __init__(self, patterns: list[_Pattern] | None = None) -> None:
        self.patterns: list[_Pattern] = patterns or []

    @classmethod
    def from_file(cls, path: Path) -> "IgnoreMatcher":
        matcher = cls()
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return matcher
        for line in text.splitlines():
            compiled = _compile_ignore(line)
            if compiled is not None:
                matcher.patterns.append(compiled)
        return matcher

    def add(self, pattern: str) -> None:
        compiled = _compile_ignore(pattern)
        if compiled is not None:
            self.patterns.append(compiled)

    def matches(self, path: str, is_dir: bool = False) -> bool:
        path = _normalize_path(path)
        ignored = False
        for pattern in self.patterns:
            # Search, not match: a non-anchored pattern such as "*.pyc" is
            # compiled with a (?:^|/) prefix and must be allowed to start at
            # an interior slash.
            match = pattern.regex.search(path)
            if not match:
                continue
            if pattern.dir_only:
                rest = path[match.end() :]
                if not (rest.startswith("/") or is_dir):
                    continue
            ignored = not pattern.negated
        return ignored

    def __bool__(self) -> bool:
        return bool(self.patterns)

    @property
    def has_negation(self) -> bool:
        return any(p.negated for p in self.patterns)


def _compile_glob(pattern: str) -> re.Pattern[str] | None:
    pattern = pattern.strip().replace("\\", "/")
    if not pattern:
        return None
    if pattern.startswith("./"):
        pattern = pattern[2:]
    dir_only = pattern.endswith("/")
    if dir_only:
        pattern = pattern.rstrip("/")
    anchored = pattern.startswith("/") or "/" in pattern
    if pattern.startswith("/"):
        pattern = pattern[1:]
    if not pattern:
        return None
    body = _translate_body(pattern)
    prefix = "^" if anchored else r"(?:^|/)"
    suffix = r"(?:/.*)?$" if dir_only else "$"
    return re.compile(prefix + body + suffix)


class GlobSet:
    """A set of user-facing include/exclude/core globs."""

    def __init__(self, patterns: list[str] | None = None) -> None:
        self.patterns = [p for p in (_compile_glob(x) for x in (patterns or [])) if p]

    def matches(self, path: str) -> bool:
        path = _normalize_path(path)
        return any(p.search(path) for p in self.patterns)

    def __bool__(self) -> bool:
        return bool(self.patterns)


def glob_match(pattern: str, path: str) -> bool:
    compiled = _compile_glob(pattern)
    return bool(compiled and compiled.search(_normalize_path(path)))
