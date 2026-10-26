"""M11: comment stripping, skeletons, import normalization and truncation."""

from __future__ import annotations

import ast
import copy
import io
import re
import tokenize
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .model import File, Status
from .tokens import TokenCounter

BODY_SENTINEL = "__FLASHPACK_BODY_{}__"
DEFAULT_MAX_LINES = 2000

_JS_LANGS = {"javascript", "typescript"}
_TS_LANG = {
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
}


def _prefix(lang: str, text: str) -> str:
    if lang in {"python", "shell", "ruby", "perl", "r", "makefile", "dockerfile", "yaml"}:
        return "# " + text
    if lang in {"javascript", "typescript", "java", "c", "cpp", "csharp", "go", "rust",
                "kotlin", "swift", "scala", "php", "dart", "json", "vue", "svelte"}:
        return "// " + text
    if lang == "sql":
        return "-- " + text
    if lang == "css":
        return "/* " + text + " */"
    if lang in {"html", "xml"}:
        return "<!-- " + text + " -->"
    return "# " + text


# ---------------------------------------------------------------------------
# M11a: comments and blank lines
# ---------------------------------------------------------------------------


def _strip_python_comments(text: str) -> str:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _strip_c_comments(text, python_hash=True, slash_comments=False)
    lines = text.splitlines()
    protected: set[int] = set()
    spans: list[tuple[int, int, int]] = []
    for token in tokens:
        if token.type == tokenize.STRING:
            for line_no in range(token.start[0], token.end[0] + 1):
                protected.add(line_no)
        elif token.type == tokenize.COMMENT:
            spans.append((token.start[0], token.start[1], token.end[1]))
    for line_no, start, _end in spans:
        if 0 < line_no <= len(lines):
            lines[line_no - 1] = lines[line_no - 1][:start].rstrip()
    result: list[str] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip() and index not in protected:
            continue
        result.append(line)
    return "\n".join(result).strip("\n")


def _remove_blank_lines_preserving_strings(text: str) -> str:
    """Remove blank lines but keep blank lines inside quoted strings."""

    result: list[str] = []
    state = "code"
    quote = ""
    for line in text.splitlines():
        in_string_at_start = state == "string"
        if line.strip() or in_string_at_start:
            result.append(line.rstrip())
        i = 0
        while i < len(line):
            ch = line[i]
            if state == "code":
                if ch in ("'", '"', "`"):
                    state = "string"
                    quote = ch
            else:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    state = "code"
                    quote = ""
            i += 1
    return "\n".join(result).strip("\n")


def _strip_c_comments(
    text: str, python_hash: bool = False, slash_comments: bool = True
) -> str:
    """Remove //, /* */ and optionally # comments without touching strings."""

    out: list[str] = []
    i = 0
    n = len(text)
    state = "code"
    quote = ""
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if state == "code":
            if ch in ("'", '"', "`"):
                state = "string"
                quote = ch
                out.append(ch)
                i += 1
                continue
            if ch == "#" and python_hash:
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if ch == "/" and nxt == "/" and slash_comments:
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if ch == "/" and nxt == "*" and slash_comments:
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
                i = min(n, i + 2)
                continue
            out.append(ch)
            i += 1
            continue
        # string state
        out.append(ch)
        if ch == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if ch == quote:
            state = "code"
            quote = ""
        i += 1
    return _remove_blank_lines_preserving_strings("".join(out))



def _strip_sql_comments(text: str) -> str:
    lines = []
    in_block = False
    for line in text.splitlines():
        if in_block:
            if "*/" in line:
                in_block = False
                line = line.split("*/", 1)[1]
            else:
                continue
        line = re.sub(r"/\*.*?\*/", "", line)
        if "/*" in line:
            line = line.split("/*", 1)[0]
            in_block = True
        if "--" in line:
            line = line.split("--", 1)[0]
        if line.strip():
            lines.append(line.rstrip())
    return "\n".join(lines).strip("\n")


def strip_comments(text: str, lang: str) -> str:
    if lang == "python":
        result = _strip_python_comments(text)
    elif lang in _JS_LANGS:
        result = _strip_c_comments(text)
    elif lang in {
        "shell",
        "ruby",
        "perl",
        "r",
        "makefile",
        "dockerfile",
        "toml",
        "yaml",
    }:
        result = _strip_c_comments(text, python_hash=True, slash_comments=False)
    elif lang == "php":
        result = _strip_c_comments(text, python_hash=True, slash_comments=True)
    elif lang == "sql":
        result = _strip_sql_comments(text)
    elif lang in {"css", "html", "xml"}:
        result = _strip_c_comments(text)
    else:
        result = _strip_c_comments(text)
    return result


# ---------------------------------------------------------------------------
# M11b: Python skeleton
# ---------------------------------------------------------------------------


def _doc_expr(node: ast.AST) -> ast.Expr | None:
    doc = ast.get_docstring(node, clean=False)
    if doc is None:
        return None
    return ast.Expr(value=ast.Constant(value=doc))


def _body_count(body: list[ast.stmt]) -> int:
    if not body:
        return 0
    start = body[0].lineno
    end = body[-1].end_lineno or start
    return max(0, end - start + 1)


def _prune_function(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    markers: dict[str, tuple[int, bool]],
) -> None:
    body = node.body
    doc = None
    rest = body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            doc = body[0]
            rest = body[1:]
    sentinel = BODY_SENTINEL.format(len(markers))
    markers[sentinel] = (_body_count(rest), doc is not None)
    new_body: list[ast.stmt] = []
    if doc is not None:
        new_body.append(doc)
    new_body.append(ast.Expr(value=ast.Constant(value=sentinel)))
    node.body = new_body


def _prune_class(node: ast.ClassDef, markers: dict[str, tuple[int, bool]]) -> None:
    body: list[ast.stmt] = []
    doc = _doc_expr(node)
    if doc is not None:
        body.append(doc)
    for stmt in node.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _prune_function(stmt, markers)
            body.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            _prune_class(stmt, markers)
            body.append(stmt)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            body.append(stmt)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            body.append(stmt)
    if not body:
        body.append(ast.Pass())
    node.body = body


def _prune_module(node: ast.Module, markers: dict[str, tuple[int, bool]]) -> None:
    body: list[ast.stmt] = []
    for stmt in node.body:
        if isinstance(stmt, (ast.Import, ast.ImportFrom)):
            body.append(stmt)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _prune_function(stmt, markers)
            body.append(stmt)
        elif isinstance(stmt, ast.ClassDef):
            _prune_class(stmt, markers)
            body.append(stmt)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            body.append(stmt)
        elif isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
            if not body and isinstance(stmt.value.value, str):
                body.append(stmt)
    if not body:
        body.append(ast.Pass())
    node.body = body


def skeleton_python(text: str) -> str | None:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError):
        return None
    markers: dict[str, tuple[int, bool]] = {}
    pruned = copy.deepcopy(tree)
    _prune_module(pruned, markers)
    if not markers:
        return None
    ast.fix_missing_locations(pruned)
    try:
        rendered = ast.unparse(pruned)
    except Exception:
        return None
    lines: list[str] = ["# [skeleton: signatures and docstrings only, bodies omitted]"]
    for line in rendered.splitlines():
        match = re.search(r"__FLASHPACK_BODY_(\d+)__", line)
        if not match:
            lines.append(line)
            continue
        sentinel = f"__FLASHPACK_BODY_{match.group(1)}__"
        count, has_doc = markers.get(sentinel, (0, False))
        indent = line[: len(line) - len(line.lstrip())]
        lines.append(f"{indent}# [body omitted, {count} lines]")
        # A comment is not a Python statement. Keep the skeleton parseable
        # when there is no docstring to act as the function body.
        if not has_doc:
            lines.append(indent + "...")
    return "\n".join(lines).strip("\n")


# ---------------------------------------------------------------------------
# M11b: optional tree-sitter JS/TS skeleton (D18)
# ---------------------------------------------------------------------------

_TS_FACTORY: Callable[[str], Any] | None | bool = None
_TS_CACHE: dict[str, Any] = {}
_TS_DOWNLOADED: set[str] | None = None


def _downloaded_languages() -> set[str]:
    """Never trigger a network download: D1 requires offline operation."""

    global _TS_DOWNLOADED
    if _TS_DOWNLOADED is None:
        try:
            from tree_sitter_language_pack import downloaded_languages  # type: ignore

            _TS_DOWNLOADED = set(downloaded_languages())
        except Exception:
            _TS_DOWNLOADED = set()
    return _TS_DOWNLOADED


def tree_sitter_available() -> bool:
    global _TS_FACTORY
    if _TS_FACTORY is None:
        try:
            from tree_sitter_language_pack import get_parser  # type: ignore

            _TS_FACTORY = get_parser
        except ImportError:
            _TS_FACTORY = False
    return bool(_TS_FACTORY) and bool(_downloaded_languages())


def _parser(lang: str) -> Any | None:
    if not tree_sitter_available():
        return None
    name = _TS_LANG.get(lang)
    if not name or name not in _downloaded_languages():
        return None
    if name in _TS_CACHE:
        return _TS_CACHE[name]
    try:
        parser = _TS_FACTORY(name)  # type: ignore[operator]
    except Exception:
        parser = None
    _TS_CACHE[name] = parser
    return parser


def _ts_slice(node: Any, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _ts_render(node: Any, source: bytes) -> str | None:
    t = node.type
    if t == "ERROR":
        return None
    if t in {"comment", "line_comment", "block_comment"}:
        return _ts_slice(node, source)
    if t == "export_statement":
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            return _ts_render(declaration, source)
        text = _ts_slice(node, source)
        return text if len(text) <= 1200 else None
    if t in {
        "import_statement",
        "import_declaration",
        "lexical_declaration",
        "variable_declaration",
    }:
        text = _ts_slice(node, source)
        return text if len(text) <= 800 else None
    body = node.child_by_field_name("body")
    if t in {
        "function_declaration",
        "function_definition",
        "method_definition",
        "method_declaration",
        "constructor_declaration",
        "function_item",
        "method",
    }:
        if body is None:
            return None
        header = source[node.start_byte : body.start_byte].decode("utf-8", "replace").rstrip()
        indent = " " * node.start_point.column
        count = body.end_point.row - body.start_point.row + 1
        return f"{header} {{\n{indent}  // [body omitted, {count} lines]\n{indent}}}"
    if t in {
        "class_declaration",
        "class_definition",
        "interface_declaration",
        "class",
        "impl_item",
        "trait_item",
    }:
        if body is None:
            return None
        header = source[node.start_byte : body.start_byte].decode("utf-8", "replace").rstrip()
        indent = " " * node.start_point.column
        members: list[str] = []
        for child in body.named_children:
            rendered = _ts_render(child, source)
            if rendered:
                members.append(rendered.rstrip())
        if not members:
            count = body.end_point.row - body.start_point.row + 1
            members.append(f"{indent}  // [body omitted, {count} lines]")
        return f"{header} {{\n" + "\n".join(members) + f"\n{indent}}}"
    if t in {"program", "module", "source_file"}:
        parts = []
        for child in node.named_children:
            rendered = _ts_render(child, source)
            if rendered:
                parts.append(rendered.rstrip())
        return "\n".join(parts) if parts else None
    return None


def skeleton_tree_sitter(text: str, lang: str) -> str | None:
    parser = _parser(lang)
    if parser is None:
        return None
    data = text.encode("utf-8", "replace")
    try:
        tree = parser.parse(data)
    except Exception:
        return None
    root = tree.root_node
    if root is None or root.has_error:
        return None
    rendered = _ts_render(root, data)
    if not rendered or not rendered.strip():
        return None
    return "// [skeleton: signatures and docstrings only, bodies omitted]\n" + rendered.strip()


# ---------------------------------------------------------------------------
# M11c: import normalization
# ---------------------------------------------------------------------------


def _is_star_import(node: ast.AST) -> bool:
    return isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)


def normalize_imports_python(text: str) -> str:
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, TypeError):
        return text
    body = tree.body
    start = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        if isinstance(body[0].value.value, str):
            start = 1
    group: list[ast.stmt] = []
    index = start
    while index < len(body) and isinstance(body[index], (ast.Import, ast.ImportFrom)):
        group.append(body[index])
        index += 1
    if not group:
        return text
    if any(_is_star_import(node) for node in group):
        return text
    names: list[str] = []
    for node in group:
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        else:
            module = "." * node.level + (node.module or "")
            if module == ".":
                names.extend(
                    "." + alias.name for alias in node.names if alias.name != "*"
                )
            elif module:
                names.append(module)
    if not names:
        return text
    names = list(dict.fromkeys(names))
    first = group[0].lineno
    last = group[-1].end_lineno or first
    lines = text.splitlines()
    comment = "# imports: " + ", ".join(names)
    return "\n".join(lines[: first - 1] + [comment] + lines[last:])


def normalize_imports_js(text: str) -> str:
    lines = text.splitlines()
    group: list[str] = []
    names: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("//"):
            index += 1
            continue
        if stripped.startswith("import ") or stripped.startswith("export "):
            if " from " not in stripped and "import " not in stripped:
                break
            group.append(lines[index])
            for match in re.finditer(r"['\"]([^'\"]+)['\"]", stripped):
                names.append(match.group(1))
            index += 1
            continue
        if "require(" in stripped:
            group.append(lines[index])
            for match in re.finditer(r"['\"]([^'\"]+)['\"]", stripped):
                names.append(match.group(1))
            index += 1
            continue
        break
    if not group or not names:
        return text
    if any("import *" in line or "from *" in line for line in group):
        return text
    names = list(dict.fromkeys(names))
    comment = "// imports: " + ", ".join(names)
    return "\n".join(lines[: index - len(group)] + [comment] + lines[index:])


def normalize_imports(text: str, lang: str) -> str:
    if not text.strip():
        return text
    if lang == "python":
        return normalize_imports_python(text)
    if lang in _JS_LANGS:
        return normalize_imports_js(text)
    return text


# ---------------------------------------------------------------------------
# M11d: truncation
# ---------------------------------------------------------------------------


def truncate_text(
    text: str,
    lang: str,
    max_lines: int,
    total_lines: int,
    force: bool = False,
) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines and not force:
        return text, False
    cut = min(max_lines, len(lines))
    if lang == "python":
        try:
            tree = ast.parse(text)
            boundaries = [
                node.end_lineno
                for node in tree.body
                if getattr(node, "end_lineno", None) is not None
                and node.end_lineno <= max_lines
            ]
            if boundaries:
                cut = max(boundaries)
        except (SyntaxError, ValueError, TypeError):
            pass
    elif lang in _JS_LANGS:
        for candidate in range(min(max_lines, len(lines)), 0, -1):
            if lines[candidate - 1].rstrip().endswith(("}", ";")):
                cut = candidate
                break
    if cut < 1:
        cut = 1
    marker = _prefix(lang, f"[truncated: showing {cut} of {total_lines} lines]")
    return "\n".join(lines[:cut] + [marker]), True


@dataclass
class Compressed:
    content: str
    status: Status
    tokens: int
    js_fallback: bool = False


def compress(file: File, config: Config, counter: TokenCounter, js_available: bool) -> Compressed:
    original = file.text or ""
    base = strip_comments(original, file.lang)
    if not base.strip():
        base = original
    content = base
    status = Status.FULL
    js_fallback = False

    if config.skeleton:
        if file.lang == "python":
            skeleton = skeleton_python(original)
            if skeleton:
                content = skeleton
                status = Status.SKELETON
        elif file.lang in _JS_LANGS:
            if js_available:
                skeleton = skeleton_tree_sitter(base, file.lang)
                if skeleton:
                    content = skeleton
                    status = Status.SKELETON
                else:
                    js_fallback = True
            else:
                js_fallback = True

    content = normalize_imports(content, file.lang)
    forced = file.size > config.max_file_size
    content, truncated = truncate_text(
        content,
        file.lang,
        DEFAULT_MAX_LINES,
        file.lines or len(original.splitlines()),
        force=forced,
    )
    if truncated:
        status = Status.TRUNCATED
    if not content.strip():
        content = original
        status = Status.FULL
    return Compressed(
        content=content,
        status=status,
        tokens=counter.count(content),
        js_fallback=js_fallback,
    )
