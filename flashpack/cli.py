"""M1/M17: CLI entry point and exit-code mapping."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from . import __version__
from .budget import apply_budget, assign_fids, prepare
from .config import load_config
from .deps import analyze
from .errors import OutputError, ParameterError, TargetError
from .render import render
from .sources import discover
from .tokens import build_counter


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise ParameterError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="flashpack",
        description="Pack a project into a single budgeted snapshot for a web AI.",
    )
    parser.add_argument("target", help="project directory")
    parser.add_argument("--format", choices=("xml", "markdown"), default=None,
                        help="output format (default: xml)")
    parser.add_argument("--budget", type=int, default=None,
                        help="token budget (default: 32000)")
    parser.add_argument("--include", action="append", default=None,
                        help="comma-separated include globs; replaces the default whitelist")
    parser.add_argument("--exclude", action="append", default=None,
                        help="comma-separated exclude globs")
    parser.add_argument("--max-file-size", type=int, default=None,
                        help="max bytes before truncation (default: 512000)")
    parser.add_argument("--tree-depth", type=int, default=None,
                        help="directory tree depth (default: 4)")
    parser.add_argument("--output", default=None,
                        help="output path (default: ./flashpack-snapshot.xml|md)")
    parser.add_argument("--no-secret-scan", action="store_true", default=None,
                        help="disable the built-in secret scan")
    parser.add_argument("--no-skeleton", action="store_true", default=None,
                        help="disable skeleton extraction")
    parser.add_argument("--timestamp", action="store_true", default=None,
                        help="include generated_at in XML (breaks byte-identical output)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def _write_output(path: Path, text: str) -> None:
    if path.exists() and path.is_dir():
        raise OutputError(f"输出路径是目录：{path}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            dir=str(path.parent), prefix=".flashpack-", suffix=".tmp"
        )
        temp = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(temp, path)
        finally:
            try:
                if temp.exists():
                    temp.unlink()
            except OSError:
                pass
    except OSError as exc:
        raise OutputError(f"无法写入输出文件：{path}") from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        config = load_config(args)
        counter, counter_warning = build_counter()

        snapshot = discover(config)
        analyze(snapshot.files)
        assign_fids(snapshot.files)
        prepare(snapshot, config, counter)

        warnings = [*config.warnings]
        if counter_warning:
            warnings.append(counter_warning)
        warnings.extend(snapshot.warnings)
        if snapshot.js_fallback:
            warnings.append(
                "tree-sitter 不可用，JS/TS 将回退为基础压缩（不做骨架提取）。"
            )

        exit_code = apply_budget(snapshot, config, counter)
        warnings.extend(snapshot.warnings)
        text = render(snapshot, config)

        # Warnings are printed once, in deterministic order.
        seen: set[str] = set()
        for warning in warnings:
            if warning not in seen:
                seen.add(warning)
                print(f"flashpack: warning: {warning}", file=sys.stderr)

        _write_output(config.output_path, text)
        return exit_code
    except ParameterError as exc:
        print(f"flashpack: error: {exc}", file=sys.stderr)
        return 1
    except TargetError as exc:
        print(f"flashpack: error: {exc}", file=sys.stderr)
        return 2
    except OutputError as exc:
        print(f"flashpack: error: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:  # pragma: no cover
        print("flashpack: interrupted", file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        print(f"flashpack: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
