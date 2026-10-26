"""M14-M16: protocol, file list, XML and Markdown rendering."""

from __future__ import annotations

import html
import re
from collections import Counter
from datetime import datetime, timezone

from .config import Config
from .model import Snapshot, Status
from .tree import render_tree

_INVALID_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _xml_attr(value: object) -> str:
    return html.escape(str(value), quote=True)


def _xml_text(value: str) -> str:
    return html.escape(_INVALID_XML.sub("", value), quote=False)


def _cdata(value: str) -> str:
    value = _INVALID_XML.sub("", value)
    return "<![CDATA[" + value.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def _status_legend() -> str:
    return (
        "full=全文；skeleton=仅签名与docstring（仅对成功解析的 Python/JS/TS 文件生效）；"
        "truncated=已截断；omitted=仅列名"
    )


def _protocol(snapshot: Snapshot) -> tuple[str, str, str, str, str]:
    files = snapshot.files
    skipped = snapshot.skipped
    non_budget = [item for item in skipped if item.reason != "budget"]
    counts = Counter(item.reason for item in non_budget)
    budget_count = snapshot.budget_omitted_count or sum(
        1 for item in skipped if item.reason == "budget"
    )

    pieces = [f"本快照覆盖 {len(files)}/{snapshot.files_total} 个项目文件"]
    if counts:
        detail = "、".join(f"{reason}={counts[reason]}" for reason in sorted(counts))
        pieces.append(f"另有 {sum(counts.values())} 个文件被排除（{detail}）")
    if budget_count:
        pieces.append(f"{budget_count} 个文件因预算省略正文")
    if snapshot.list_mode:
        if snapshot.budget_low:
            pieces.append("预算极低，已进入清单模式，全部正文省略")
        else:
            pieces.append("已进入清单模式，全部正文省略")
    if snapshot.ignored_dirs:
        pieces.append(f"{snapshot.ignored_dirs} 个依赖/缓存目录已忽略")
    coverage = "；".join(pieces) + "。"

    warning = "骨架/截断文件的实现细节未提供，请勿臆测。"
    if snapshot.js_fallback:
        warning += (
            " 当前环境未启用 tree-sitter，JS/TS 未做骨架提取，"
            "仅执行注释剥离、import 归一化与截断。"
        )
    return (
        coverage,
        _status_legend(),
        "引用文件请用 ID（如 F003），路径仅供人类阅读。",
        "正文已按被引用次数降序排列，F001 为最核心文件，建议从 F001 开始阅读。",
        warning,
    )


def _estimated(snapshot: Snapshot) -> int:
    return sum(file.tokens for file in snapshot.files)


def _skipped_lines(snapshot: Snapshot) -> list[str]:
    result = []
    for item in snapshot.skipped_sorted():
        result.append(f"- {item.path} — {item.reason}")
    return result


def _fence(content: str) -> str:
    longest = max((len(m.group(0)) for m in re.finditer(r"`+", content)), default=0)
    return "`" * max(4, longest + 1)


def _md_path(path: str) -> str:
    return path.replace("|", "&#124;").replace("\n", " ")


def render_markdown(snapshot: Snapshot, config: Config) -> str:
    files = snapshot.sorted_files()
    coverage, legend, reference, reading, warning = _protocol(snapshot)
    estimated = _estimated(snapshot)
    lines: list[str] = [
        f"# 项目快照：{snapshot.project}",
        "",
        "## 0. 阅读协议",
        "",
        f"- {coverage}",
        f"- 状态：{legend}。",
        f"- {reference}",
        f"- {reading}",
        f"- {warning}",
        "",
        "## 1. 目录树",
        "",
        "```",
        render_tree([file.path for file in files], snapshot.project, config.tree_depth),
        "```",
        "",
        "## 2. 文件清单",
        "",
        f"共 {len(files)} 个文件，估算 {estimated:,} tokens（预算 {config.budget}）。",
        "",
        "| ID | 路径 | 语言 | 行数 | Tokens | 状态 | 被引用次数 |",
        "|----|------|------|------|--------|------|------------|",
    ]
    for file in files:
        lines.append(
            f"| {file.fid} | {_md_path(file.path)} | {file.lang} | {file.lines} "
            f"| {file.tokens:,} | {file.status.value} | {file.refs} |"
        )
    if not files:
        lines.append("（无）")
    lines.extend(["", "## 2.5 依赖索引", ""])
    lines.append("格式：`ID 路径 → 依赖的 FID 列表`（无内部依赖写 `—`）")
    lines.append("")
    for file in files:
        dependency = ", ".join(file.depends_on) if file.depends_on else "—"
        lines.append(f"- {file.fid} {file.path} → {dependency}")
    if not files:
        lines.append("（无）")

    lines.extend(["", "## 3. 文件内容", ""])
    content_files = [file for file in files if file.status is not Status.OMITTED]
    if not content_files:
        if snapshot.list_mode:
            if snapshot.budget_low:
                lines.append("（无：预算极低，已省略全部正文）")
            else:
                lines.append("（无：预算无法满足，已省略全部正文）")
        else:
            lines.append("（无）")
    for file in content_files:
        marker = ""
        if file.status is Status.SKELETON:
            marker = "  ⚠️ skeleton"
        elif file.status is Status.TRUNCATED:
            marker = "  ⚠️ truncated"
        lines.extend(["", f"### [{file.fid}] {file.path}{marker}", ""])
        if file.status is Status.SKELETON:
            lines.append("> 仅保留签名与 docstring，函数体省略。")
            lines.append("")
        elif file.status is Status.TRUNCATED:
            lines.append("> 已截断，仅保留前部内容。")
            lines.append("")
        fence = _fence(file.content)
        lines.append(fence + (file.lang or ""))
        lines.append(file.content)
        lines.append(fence)

    omitted = [file for file in files if file.status is Status.OMITTED]
    if omitted:
        if snapshot.list_mode:
            lines.extend(["", "> 注：清单模式下所有正文省略。"])
        else:
            lines.extend(["", "> 注：omitted 文件未展开，详见第 4 节。"])

    lines.extend(["", "## 4. 被排除文件", ""])
    skipped = _skipped_lines(snapshot)
    lines.extend(skipped if skipped else ["（无）"])
    return "\n".join(lines).rstrip() + "\n"


def render_xml(snapshot: Snapshot, config: Config) -> str:
    files = snapshot.sorted_files()
    coverage, legend, reference, reading, warning = _protocol(snapshot)
    estimated = _estimated(snapshot)
    attrs = [f'project="{_xml_attr(snapshot.project)}"']
    if config.timestamp:
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        attrs.append(f'generated_at="{generated}"')
    attrs.extend(
        [
            f'files_included="{len(files)}"',
            f'files_total="{snapshot.files_total}"',
            f'total_lines="{sum(file.lines for file in files)}"',
            f'estimated_tokens="{estimated}"',
            f'budget="{config.budget}"',
            'sort="refs-desc"',
            f'skeleton="{"on" if config.skeleton else "off"}"',
        ]
    )
    lines: list[str] = ["<snapshot " + " ".join(attrs) + ">"]
    lines.extend(
        [
            "  <protocol>",
            f"    <coverage>{_xml_text(coverage)}</coverage>",
            f"    <status-legend>{_xml_text(legend)}</status-legend>",
            f"    <reference-rule>{_xml_text(reference)}</reference-rule>",
            f"    <reading-order>{_xml_text(reading)}</reading-order>",
            f"    <warning>{_xml_text(warning)}</warning>",
            "  </protocol>",
            "  <tree>",
            _xml_text(render_tree([file.path for file in files], snapshot.project, config.tree_depth)),
            "  </tree>",
        ]
    )
    if files:
        lines.append("  <file-list>")
        for file in files:
            depends = ",".join(file.depends_on)
            lines.append(
                f'    <file id="{file.fid}" path="{_xml_attr(file.path)}" '
                f'lang="{_xml_attr(file.lang)}" lines="{file.lines}" '
                f'tokens="{file.tokens}" status="{file.status.value}" '
                f'refs="{file.refs}" depends-on="{_xml_attr(depends)}"/>'
            )
        lines.append("  </file-list>")
    else:
        lines.append("  <file-list/>")

    content_files = [file for file in files if file.status is not Status.OMITTED]
    if not content_files:
        lines.append("  <contents/>")
    else:
        lines.append("  <contents>")
        for file in content_files:
            lines.append(
                f'    <file id="{file.fid}" path="{_xml_attr(file.path)}" '
                f'status="{file.status.value}">'
            )
            lines.append("      " + _cdata(file.content))
            lines.append("    </file>")
        lines.append("  </contents>")

    if snapshot.skipped:
        lines.append("  <skipped>")
        for item in snapshot.skipped_sorted():
            lines.append(
                f'    <file path="{_xml_attr(item.path)}" reason="{_xml_attr(item.reason)}"/>'
            )
        lines.append("  </skipped>")
    lines.append("</snapshot>")
    return "\n".join(lines) + "\n"


def render(snapshot: Snapshot, config: Config) -> str:
    if config.format == "markdown":
        return render_markdown(snapshot, config)
    return render_xml(snapshot, config)
