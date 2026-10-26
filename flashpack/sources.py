"""M3-M6: discovery, filtering, secret scanning and decoding."""

from __future__ import annotations

import fnmatch
import math
import os
import re
from pathlib import Path

from .config import Config, LOCKFILES
from .ignore import IgnoreMatcher, glob_match
from .model import File, Skipped, Snapshot, Status

# M3: the frozen built-in blacklist. Anything else is left to .gitignore/user.
HARD_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    "dist",
    "build",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
}

LANG_BY_EXT = {
    ".py": "python",
    ".pyi": "python",
    ".pyx": "python",
    ".pxd": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".java": "java",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".scala": "scala",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".dart": "dart",
    ".lua": "lua",
    ".pl": "perl",
    ".pm": "perl",
    ".r": "r",
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".cljc": "clojure",
    ".fs": "fsharp",
    ".fsi": "fsharp",
    ".fsx": "fsharp",
    ".vb": "vbnet",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".fish": "shell",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".sass": "css",
    ".less": "css",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",
    ".sql": "sql",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".xml": "xml",
    ".md": "markdown",
    ".txt": "text",
}

LANG_BY_NAME = {
    "pyproject.toml": "toml",
    "cargo.toml": "toml",
    "setup.py": "python",
    "setup.cfg": "text",
    "requirements.txt": "text",
    "package.json": "json",
    "tsconfig.json": "json",
    "jsconfig.json": "json",
    "go.mod": "text",
    "pom.xml": "xml",
    "build.gradle": "text",
    "build.gradle.kts": "text",
    "settings.gradle": "text",
    "settings.gradle.kts": "text",
    "makefile": "makefile",
    "dockerfile": "dockerfile",
    "docker-compose.yml": "yaml",
    "docker-compose.yaml": "yaml",
}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "api-key-assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-/+]{16,})"
        ),
    ),
    (
        "high-entropy-assignment",
        re.compile(
            r"(?i)(?:key|token|secret|password|passwd)\s*[:=]\s*['\"]?"
            r"(?=[A-Za-z0-9_\-/+]*[A-Z])(?=[A-Za-z0-9_\-/+]*[a-z])"
            r"(?=[A-Za-z0-9_\-/+]*[0-9])[A-Za-z0-9_\-/+]{20,}"
        ),
    ),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
]

_GENERIC_QUOTED = re.compile(r"[\"']([A-Za-z0-9_\-+/=]{21,})[\"']")


class PatternMatcher:
    """Case-insensitive include/exclude matcher.

    Patterns without ``/`` match the basename (the frozen default include
    contract); patterns with ``/`` match the relative path. A no-slash token
    also excludes descendants, so ``--exclude vendor`` behaves intuitively.
    """

    def __init__(self, patterns: list[str]) -> None:
        self.patterns = [p.replace("\\", "/").strip() for p in patterns if p.strip()]

    def matches(self, path: str) -> bool:
        base = path.rsplit("/", 1)[-1].lower()
        lowered = path.lower()
        for pattern in self.patterns:
            p = pattern.lower()
            if "/" in p:
                if glob_match(p, lowered):
                    return True
                continue
            if fnmatch.fnmatchcase(base, p):
                return True
            if lowered.startswith(p.rstrip("/") + "/"):
                return True
        return False


def _lang_for(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    if name in LANG_BY_NAME:
        return LANG_BY_NAME[name]
    suffix = Path(name).suffix.lower()
    return LANG_BY_EXT.get(suffix, "text")


def _read_head(path: Path, limit: int = 8192) -> bytes:
    with path.open("rb") as handle:
        return handle.read(limit)


def _count_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            count = 0
            last = b""
            while True:
                chunk = handle.read(65536)
                if not chunk:
                    break
                count += chunk.count(b"\n")
                last = chunk[-1:]
            if last and last != b"\n":
                count += 1
            return count
    except OSError:
        return 0


def _decode(data: bytes) -> tuple[str, str]:
    """UTF-8 (BOM stripped) first, then latin-1; normalize newlines."""

    try:
        text = data.decode("utf-8-sig")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        encoding = "latin-1"
    text = text.replace("\x00", "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text, encoding


def _secret_hits(text: str) -> list[tuple[int, str]]:
    """Return (line, type) hits. Never return the matched value itself."""

    sample = text[:1024]
    hits: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for kind, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(sample):
            line = sample.count("\n", 0, match.start()) + 1
            key = (line, kind)
            if key not in seen:
                seen.add(key)
                hits.append(key)
    # Generic high-entropy quoted strings: conservative Shannon check.
    for match in _GENERIC_QUOTED.finditer(sample):
        value = match.group(1)
        if not (
            any(ch.isupper() for ch in value)
            and any(ch.islower() for ch in value)
            and any(ch.isdigit() for ch in value)
        ):
            continue
        counts = Counter(value)
        entropy = -sum(
            (count / len(value)) * math.log2(count / len(value))
            for count in counts.values()
        )
        if entropy < 3.5:
            continue
        line = sample.count("\n", 0, match.start()) + 1
        key = (line, "high-entropy")
        if key not in seen:
            seen.add(key)
            hits.append(key)
    return sorted(hits)


def _line_count_for_text(text: str) -> int:
    if not text:
        return 0
    return len(text.splitlines())


def discover(config: Config) -> Snapshot:
    target = config.target
    project = config.project
    snapshot = Snapshot(
        root=target,
        project=project,
        budget=config.budget,
        warnings=[],
    )

    ignore = (
        IgnoreMatcher.from_file(target / ".gitignore")
        if (target / ".gitignore").is_file()
        else IgnoreMatcher()
    )
    include_matcher = PatternMatcher(config.include)
    exclude_matcher = PatternMatcher(config.exclude)

    files_total = 0
    ignored_dirs = 0
    warnings: list[str] = []
    skipped: list[Skipped] = []

    def on_walk_error(error: OSError) -> None:
        warnings.append(f"目录不可读，已跳过：{getattr(error, 'filename', '')}".rstrip("："))

    for dirpath, dirnames, filenames in os.walk(target, followlinks=False, onerror=on_walk_error):
        rel_dir = Path(dirpath).relative_to(target).as_posix()
        if rel_dir == ".":
            rel_dir = ""
        keep: list[str] = []
        for name in sorted(dirnames):
            full = Path(dirpath) / name
            if name in HARD_DIRS:
                ignored_dirs += 1
                continue
            if full.is_symlink():
                continue
            keep.append(name)
        dirnames[:] = keep

        for name in sorted(filenames):
            full = Path(dirpath) / name
            rel = f"{rel_dir}/{name}".lstrip("/")
            if full.is_symlink():
                continue
            if ignore.matches(rel):
                continue
            try:
                if not full.is_file():
                    continue
                size = full.stat().st_size
            except OSError:
                warnings.append(f"文件不可读，已跳过：{rel}")
                continue

            # The default include list is a language whitelist, not a list of
            # "excluded" files. Files outside it are out of scope and are not
            # counted as skipped; only included candidates are reported.
            if not include_matcher.matches(rel):
                continue
            files_total += 1

            # M4 step 1: binary check.
            try:
                head = _read_head(full)
            except OSError:
                warnings.append(f"文件不可读，已跳过：{rel}")
                continue
            if b"\x00" in head:
                skipped.append(Skipped(rel, "binary"))
                continue

            oversized = size > config.max_file_size
            if exclude_matcher.matches(rel):
                skipped.append(Skipped(rel, "excluded"))
                continue
            if name.lower() in LOCKFILES:
                skipped.append(Skipped(rel, "lockfile"))
                continue

            # M4 step 2: oversized files are kept but truncated later.
            read_limit = config.max_file_size if oversized else size + 1
            try:
                data = _read_head(full, read_limit)
            except OSError:
                warnings.append(f"文件不可读，已跳过：{rel}")
                continue

            try:
                text, encoding = _decode(data)
            except UnicodeDecodeError:
                entry = File(
                    path=rel,
                    abs_path=full,
                    lang=_lang_for(rel),
                    lines=_count_lines(full),
                    size=size,
                    status=Status.OMITTED,
                    reason="encoding-error",
                )
                snapshot.files.append(entry)
                skipped.append(Skipped(rel, "encoding-error"))
                warnings.append(f"编码失败，已标 omitted：{rel}")
                continue

            if not text.strip():
                skipped.append(Skipped(rel, "empty"))
                continue

            # M5: secret scan on the first 1 KB. A hit blocks the file.
            if config.secret_scan:
                hits = _secret_hits(text)
                if hits:
                    for line, kind in hits:
                        warnings.append(
                            f"密钥扫描命中：{rel}:{line} ({kind})，已阻止打包该文件；"
                            "如需排除请使用 --exclude。"
                        )
                    skipped.append(Skipped(rel, "secret"))
                    continue

            lines = _count_lines(full) if oversized else _line_count_for_text(text)
            entry = File(
                path=rel,
                abs_path=full,
                lang=_lang_for(rel),
                lines=lines,
                size=size,
                status=Status.FULL,
                text=text,
                content=text,
                encoding=encoding,
                reason="truncated" if oversized else "",
            )
            snapshot.files.append(entry)

    snapshot.files_total = files_total
    snapshot.ignored_dirs = ignored_dirs
    snapshot.skipped = sorted(skipped, key=lambda item: item.path)
    snapshot.warnings = warnings
    return snapshot
