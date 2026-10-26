"""M9: depth-limited directory tree."""

from __future__ import annotations


class _Node:
    __slots__ = ("dirs", "files")

    def __init__(self) -> None:
        self.dirs: dict[str, _Node] = {}
        self.files: list[str] = []

    def has_content(self) -> bool:
        return bool(self.files or self.dirs)


def _insert(root: _Node, path: str) -> None:
    node = root
    parts = path.split("/")
    for part in parts[:-1]:
        node = node.dirs.setdefault(part, _Node())
    node.files.append(parts[-1])


def _render(node: _Node, prefix: str, depth: int, max_depth: int, lines: list[str]) -> None:
    entries: list[tuple[str, str]] = []
    if depth < max_depth:
        for name in sorted(node.dirs):
            entries.append(("dir", name))
    for name in sorted(node.files):
        entries.append(("file", name))
    for index, (kind, name) in enumerate(entries):
        last = index == len(entries) - 1 and not (node.dirs and depth >= max_depth)
        connector = "└── " if last else "├── "
        if kind == "file":
            lines.append(prefix + connector + name)
            continue
        child = node.dirs[name]
        lines.append(prefix + connector + name + "/")
        child_prefix = prefix + ("    " if last else "│   ")
        _render(child, child_prefix, depth + 1, max_depth, lines)
    if node.dirs and depth >= max_depth:
        lines.append(prefix + "└── ...")


def render_tree(paths: list[str], project: str, max_depth: int = 4) -> str:
    root = _Node()
    for path in sorted(set(paths)):
        _insert(root, path)
    if not root.has_content():
        return "（无）"
    lines = [project + "/"]
    _render(root, "", 0, max_depth, lines)
    return "\n".join(lines)
