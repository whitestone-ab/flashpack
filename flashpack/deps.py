"""M7: internal dependency graph for Python/JS/TS."""

from __future__ import annotations

import ast
import posixpath
import re

from .compress import strip_comments
from .model import File

_JS_IMPORT = re.compile(
    r"""(?x)
    (?:import|export)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"]+)['"]
    |
    require\s*\(\s*['"]([^'"]+)['"]\s*\)
    |
    import\s*\(\s*['"]([^'"]+)['"]\s*\)
    """
)

_GENERIC_INCLUDE = re.compile(r"#\s*include\s+[\"']([^\"']+)[\"']")
_GENERIC_REQUIRE = re.compile(
    r"(?:require|require_relative|include)\s*\(?\s*[\"']([^\"']+)[\"']"
)

_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_TS_EXTS = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")


def _python_module_map(files: list[File]) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for file in files:
        if file.lang != "python":
            continue
        path = file.path
        if not path.endswith(".py"):
            continue
        parts = path[:-3].split("/")
        if parts and parts[-1] == "__init__":
            parts = parts[:-1]
        for index in range(len(parts)):
            key = ".".join(parts[index:])
            mapping.setdefault(key, []).append(path)
    for key in mapping:
        mapping[key].sort()
    return mapping


def _resolve_module(name: str, mapping: dict[str, list[str]]) -> str | None:
    if not name:
        return None
    parts = name.split(".")
    candidates = [name]
    for index in range(1, len(parts)):
        candidates.append(".".join(parts[index:]))
    for candidate in candidates:
        paths = mapping.get(candidate)
        if paths:
            return paths[0]
    return None


def _relative_name(current: str, level: int, module: str | None) -> str | None:
    package = current.split("/")[:-1]
    ups = max(0, level - 1)
    if ups:
        package = package[: max(0, len(package) - ups)]
    base = ".".join(part for part in package if part)
    if module:
        return f"{base}.{module}" if base else module
    return base or None


def _python_targets(
    current: str,
    mapping: dict[str, list[str]],
    node: ast.AST,
) -> set[str]:
    targets: set[str] = set()
    if isinstance(node, ast.Import):
        for alias in node.names:
            resolved = _resolve_module(alias.name, mapping)
            if resolved:
                targets.add(resolved)
    elif isinstance(node, ast.ImportFrom):
        base_name = node.module
        if node.level:
            base_name = _relative_name(current, node.level, node.module)
        if not base_name:
            return targets
        resolved = _resolve_module(base_name, mapping)
        if resolved:
            targets.add(resolved)
        for alias in node.names:
            if alias.name == "*":
                continue
            resolved = _resolve_module(f"{base_name}.{alias.name}", mapping)
            if resolved:
                targets.add(resolved)
    return targets


def _js_targets(current: str, text: str, known: set[str]) -> set[str]:
    targets: set[str] = set()
    for match in _JS_IMPORT.finditer(text):
        spec = next((group for group in match.groups() if group), "")
        if not spec:
            continue
        if not (spec.startswith(".") or spec.startswith("/")):
            continue
        if spec.startswith("/"):
            base = spec.lstrip("/")
        else:
            base = posixpath.normpath(posixpath.join(posixpath.dirname(current), spec))
        if base.startswith("../") or base == "..":
            continue
        candidates = [base]
        extensions = _TS_EXTS if current.endswith((".ts", ".tsx", ".mts", ".cts")) else _JS_EXTS
        candidates.extend(base + ext for ext in extensions)
        candidates.extend(posixpath.join(base, "index" + ext) for ext in extensions)
        for candidate in candidates:
            if candidate in known:
                targets.add(candidate)
                break
    return targets


def _generic_targets(current: str, text: str, known: set[str], lang: str) -> set[str]:
    """Conservative fallback for C/C++ includes and dynamic require()."""

    targets: set[str] = set()
    if lang in {"c", "cpp"}:
        patterns = [_GENERIC_INCLUDE]
        extensions = (".h", ".hpp", ".c", ".cpp", ".cc", ".cxx")
    elif lang in {"php", "ruby", "perl"}:
        patterns = [_GENERIC_REQUIRE]
        extensions = (".php", ".rb", ".pl", ".pm")
    else:
        return targets
    for pattern in patterns:
        for match in pattern.finditer(text):
            spec = match.group(1)
            if not spec.startswith((".", "/")):
                continue
            base = (
                spec.lstrip("/")
                if spec.startswith("/")
                else posixpath.normpath(posixpath.join(posixpath.dirname(current), spec))
            )
            if base.startswith("../") or base == "..":
                continue
            for candidate in [base, *(base + ext for ext in extensions)]:
                if candidate in known:
                    targets.add(candidate)
                    break
    return targets


def analyze(files: list[File]) -> None:
    known = {file.path for file in files}
    python_map = _python_module_map(files)
    edges: dict[str, set[str]] = {file.path: set() for file in files}

    for file in files:
        if not file.text:
            continue
        if file.lang == "python":
            try:
                tree = ast.parse(file.text)
            except (SyntaxError, ValueError, TypeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for target in _python_targets(file.path, python_map, node):
                        if target != file.path and target in known:
                            edges[file.path].add(target)
        elif file.lang in ("javascript", "typescript"):
            stripped = strip_comments(file.text, file.lang)
            for target in _js_targets(file.path, stripped, known):
                if target != file.path:
                    edges[file.path].add(target)
        else:
            for target in _generic_targets(file.path, file.text, known, file.lang):
                if target != file.path:
                    edges[file.path].add(target)

    incoming: dict[str, set[str]] = {path: set() for path in known}
    for source, targets in edges.items():
        for target in targets:
            incoming[target].add(source)

    for file in files:
        file.refs = len(incoming[file.path])
        file.depends_on = sorted(edges[file.path])
