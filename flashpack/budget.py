"""M10/M12/M13: FIDs, final states and the two-stage budget."""

from __future__ import annotations

from dataclasses import dataclass

from .compress import compress, truncate_text, tree_sitter_available
from .config import Config
from .model import File, Skipped, Snapshot, Status
from .render import render
from .tokens import TokenCounter


def assign_fids(files: list[File]) -> None:
    """D13: refs descending, path ascending; D5: continuous F001..."""

    ordered = sorted(files, key=lambda file: (-file.refs, file.path))
    fid_map: dict[str, str] = {}
    for index, file in enumerate(ordered, start=1):
        file.fid = f"F{index:03d}"
        fid_map[file.path] = file.fid
    for file in files:
        file.depends_on = sorted(
            (fid_map[path] for path in file.depends_on if path in fid_map),
            key=lambda fid: int(fid[1:]),
        )


@dataclass
class Prepared:
    js_available: bool


def prepare(snapshot: Snapshot, config: Config, counter: TokenCounter) -> Prepared:
    js_available = tree_sitter_available()
    for file in snapshot.files:
        if file.status is Status.OMITTED:
            file.tokens = 0
            file.content = ""
            continue
        compressed = compress(file, config, counter, js_available)
        file.content = compressed.content
        file.status = compressed.status
        file.tokens = compressed.tokens
        if compressed.js_fallback:
            snapshot.js_fallback = True
    snapshot.total_lines = sum(file.lines for file in snapshot.files)
    snapshot.total_bytes = sum(file.size for file in snapshot.files)
    snapshot.estimated_tokens = sum(file.tokens for file in snapshot.files)
    snapshot.token_name = counter.name
    return Prepared(js_available=js_available)


def _add_budget_skip(snapshot: Snapshot, file: File) -> None:
    if not any(item.path == file.path and item.reason == "budget" for item in snapshot.skipped):
        snapshot.skipped.append(Skipped(file.path, "budget"))


def _drop_budget_skips(snapshot: Snapshot) -> None:
    snapshot.skipped = [item for item in snapshot.skipped if item.reason != "budget"]


def _victim(snapshot: Snapshot) -> File | None:
    candidates = [file for file in snapshot.files if file.status is not Status.OMITTED]
    if not candidates:
        return None
    # Skeleton files are already the smallest useful form and are preserved
    # until no non-skeleton candidate remains (M13).
    candidates.sort(
        key=lambda file: (file.status is Status.SKELETON, file.refs, file.path)
    )
    return candidates[0]


def _truncate_for_budget(file: File, budget: int, counter: TokenCounter) -> None:
    if file.tokens <= budget:
        return
    # Leave room for the protocol/tree/file-list; a single oversized file must
    # become truncated rather than immediately disappearing as omitted.
    target = max(20, budget // 2)
    total = file.lines or len(file.content.splitlines())
    keep = max(1, int(len(file.content.splitlines()) * target / max(1, file.tokens)))
    content, _ = truncate_text(file.content, file.lang, keep, total, force=True)
    file.content = content
    file.status = Status.TRUNCATED
    file.tokens = counter.count(content)


def apply_budget(snapshot: Snapshot, config: Config, counter: TokenCounter) -> int:
    """Return the M17 exit code contribution (0 or 4)."""

    budget = config.budget
    if budget < 1000:
        snapshot.list_mode = True
        snapshot.budget_low = True
        for file in snapshot.files:
            file.status = Status.OMITTED
            file.content = ""
            if file.reason != "encoding-error":
                file.reason = "budget"
        snapshot.budget_omitted_count = sum(
            1 for file in snapshot.files if file.reason == "budget"
        )
        _drop_budget_skips(snapshot)
        snapshot.warnings.append(
            "budget 低于 1000，已进入清单模式；全部正文省略。"
        )
        text = render(snapshot, config)
        used = counter.count(text)
        snapshot.estimated_tokens = sum(file.tokens for file in snapshot.files)
        return 4 if used > budget else 0

    # "Single file over budget -> force truncate" before global omission.
    for file in snapshot.files:
        if file.status is not Status.OMITTED:
            _truncate_for_budget(file, budget, counter)

    text = render(snapshot, config)
    used = counter.count(text)

    while used > budget:
        victim = _victim(snapshot)
        if victim is None:
            break
        victim.status = Status.OMITTED
        victim.content = ""
        victim.reason = "budget"
        snapshot.budget_omitted_count += 1
        _add_budget_skip(snapshot, victim)
        text = render(snapshot, config)
        used = counter.count(text)

    if used <= budget:
        snapshot.estimated_tokens = sum(file.tokens for file in snapshot.files)
        return 0

    # Stage 2: list mode. All bodies omitted; budget skips are folded into the
    # coverage sentence (D19).
    snapshot.list_mode = True
    for file in snapshot.files:
        file.status = Status.OMITTED
        file.content = ""
        if file.reason != "encoding-error":
            file.reason = "budget"
    snapshot.budget_omitted_count = sum(
        1 for file in snapshot.files if file.reason == "budget"
    )
    _drop_budget_skips(snapshot)
    snapshot.warnings.append(
        "预算无法满足，已进入清单模式；所有正文省略。"
    )
    text = render(snapshot, config)
    used = counter.count(text)
    snapshot.estimated_tokens = sum(file.tokens for file in snapshot.files)
    return 4 if used > budget else 0
