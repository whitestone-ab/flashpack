"""Configuration loading (M1/M2) and the frozen defaults."""

from __future__ import annotations

import argparse
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ParameterError, TargetError

DEFAULT_BUDGET = 32_000
DEFAULT_MAX_FILE_SIZE = 500 * 1024
DEFAULT_TREE_DEPTH = 4
DEFAULT_FORMAT = "xml"

# D1/M1 supplement: the default include list is a basename whitelist.
# Explicit --include completely replaces this list.
DEFAULT_INCLUDE: list[str] = [
    # Python
    "*.py", "*.pyi", "*.pyx", "*.pxd",
    # JavaScript / TypeScript
    "*.js", "*.jsx", "*.mjs", "*.cjs",
    "*.ts", "*.tsx", "*.mts", "*.cts",
    # JVM
    "*.java", "*.kt", "*.kts", "*.scala",
    # C / C++
    "*.c", "*.h", "*.cpp", "*.cc", "*.cxx",
    "*.hpp", "*.hh", "*.hxx",
    # Other common languages
    "*.cs", "*.go", "*.rs", "*.rb", "*.php",
    "*.swift", "*.dart", "*.lua", "*.pl", "*.pm",
    "*.r", "*.ex", "*.exs", "*.erl", "*.hrl",
    "*.hs", "*.clj", "*.cljs", "*.cljc",
    "*.fs", "*.fsi", "*.fsx", "*.vb",
    # Shell
    "*.sh", "*.bash", "*.zsh", "*.fish",
    # Web / templates / styles / SQL
    "*.html", "*.htm", "*.css", "*.scss", "*.sass", "*.less",
    "*.vue", "*.svelte", "*.astro", "*.sql",
    # Key build/dependency manifests (matched by basename)
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "package.json", "tsconfig.json", "jsconfig.json",
    "Cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "build.gradle.kts",
    "settings.gradle", "settings.gradle.kts",
    "Makefile", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
]

LOCKFILES = {
    "package-lock.json",
    "npm-shrinkwrap.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "cargo.lock",
    "poetry.lock",
    "pipfile.lock",
    "uv.lock",
    "composer.lock",
    "gemfile.lock",
    "go.sum",
}


@dataclass
class Config:
    target: Path
    format: str = DEFAULT_FORMAT
    budget: int = DEFAULT_BUDGET
    include: list[str] = field(default_factory=lambda: list(DEFAULT_INCLUDE))
    exclude: list[str] = field(default_factory=list)
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    tree_depth: int = DEFAULT_TREE_DEPTH
    output: str = ""
    secret_scan: bool = True
    skeleton: bool = True
    timestamp: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def project(self) -> str:
        return self.target.name or self.target.anchor or "project"

    @property
    def effective_output(self) -> str:
        if self.output:
            return self.output
        suffix = "xml" if self.format == "xml" else "md"
        return f"./flashpack-snapshot.{suffix}"

    @property
    def output_path(self) -> Path:
        path = Path(self.effective_output).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path


def _read_toml(target: Path) -> tuple[dict[str, Any], list[str]]:
    path = target / ".flashpack.toml"
    if not path.is_file():
        return {}, []
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError, ValueError):
        return {}, ["配置文件 .flashpack.toml 解析失败，已回退默认配置。"]
    if not isinstance(data, dict):
        return {}, ["配置文件 .flashpack.toml 结构无效，已回退默认配置。"]
    section = data.get("flashpack", data)
    if not isinstance(section, dict):
        return {}, ["配置文件 .flashpack.toml 结构无效，已回退默认配置。"]
    return section, []


def _get(section: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in section:
            return section[key]
    return None


def _as_int(value: Any, default: int, key: str, warnings: list[str], minimum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        warnings.append(f"配置项 {key} 无效，已回退默认值。")
        return default
    return value


def _as_bool(value: Any, default: bool, key: str, warnings: list[str]) -> bool:
    if value is None:
        return default
    if not isinstance(value, bool):
        warnings.append(f"配置项 {key} 无效，已回退默认值。")
        return default
    return value


def _as_str(value: Any, default: str, key: str, warnings: list[str]) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        warnings.append(f"配置项 {key} 无效，已回退默认值。")
        return default
    return value


def _as_list(value: Any, default: list[str], key: str, warnings: list[str]) -> list[str]:
    if value is None:
        return list(default)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        warnings.append(f"配置项 {key} 无效，已回退默认值。")
        return list(default)
    return [item for item in value if item.strip()]


def _split_patterns(values: list[str] | None) -> list[str] | None:
    if values is None:
        return None
    result: list[str] = []
    for value in values:
        result.extend(item.strip() for item in value.split(",") if item.strip())
    return result


def load_config(args: argparse.Namespace) -> Config:
    target = Path(args.target).expanduser()
    try:
        target = target.resolve()
    except OSError as exc:
        raise TargetError(f"目标目录无效：{args.target}") from exc
    if not target.exists():
        raise TargetError(f"目标目录不存在：{args.target}")
    if not target.is_dir():
        raise TargetError(f"目标不是目录：{args.target}")

    section, warnings = _read_toml(target)

    fmt = args.format if args.format is not None else _as_str(
        _get(section, "format"), DEFAULT_FORMAT, "format", warnings
    )
    fmt = fmt.lower()
    if fmt == "md":
        fmt = "markdown"
    if fmt not in ("xml", "markdown"):
        if args.format is not None:
            raise ParameterError(f"不支持的输出格式：{fmt}")
        warnings.append("配置项 format 无效，已回退默认值。")
        fmt = DEFAULT_FORMAT

    budget = (
        args.budget
        if args.budget is not None
        else _as_int(_get(section, "budget"), DEFAULT_BUDGET, "budget", warnings, 1)
    )
    if budget < 1:
        raise ParameterError("--budget 必须大于 0")

    max_file_size = (
        args.max_file_size
        if args.max_file_size is not None
        else _as_int(
            _get(section, "max_file_size", "max-file-size"),
            DEFAULT_MAX_FILE_SIZE,
            "max_file_size",
            warnings,
            1,
        )
    )
    if max_file_size < 1:
        raise ParameterError("--max-file-size 必须大于 0")

    tree_depth = (
        args.tree_depth
        if args.tree_depth is not None
        else _as_int(
            _get(section, "tree_depth", "tree-depth"),
            DEFAULT_TREE_DEPTH,
            "tree_depth",
            warnings,
            1,
        )
    )
    if tree_depth < 1:
        raise ParameterError("--tree-depth 必须大于 0")

    cli_include = _split_patterns(args.include)
    if cli_include is not None:
        include = cli_include
    else:
        include = _as_list(
            _get(section, "include"), DEFAULT_INCLUDE, "include", warnings
        )

    cli_exclude = _split_patterns(args.exclude)
    if cli_exclude is not None:
        exclude = cli_exclude
    else:
        exclude = _as_list(_get(section, "exclude"), [], "exclude", warnings)

    output = (
        args.output
        if args.output is not None
        else _as_str(_get(section, "output"), "", "output", warnings)
    )

    if args.no_secret_scan is not None:
        secret_scan = not args.no_secret_scan
    else:
        secret_scan = _as_bool(
            _get(section, "secret_scan", "secret-scan"),
            True,
            "secret_scan",
            warnings,
        )

    if args.no_skeleton is not None:
        skeleton = not args.no_skeleton
    else:
        skeleton = _as_bool(
            _get(section, "skeleton"), True, "skeleton", warnings
        )

    if args.timestamp is not None:
        timestamp = args.timestamp
    else:
        timestamp = _as_bool(
            _get(section, "timestamp"), False, "timestamp", warnings
        )

    config = Config(
        target=target,
        format=fmt,
        budget=budget,
        include=include,
        exclude=exclude,
        max_file_size=max_file_size,
        tree_depth=tree_depth,
        output=output,
        secret_scan=secret_scan,
        skeleton=skeleton,
        timestamp=timestamp,
        warnings=warnings,
    )
    return config
