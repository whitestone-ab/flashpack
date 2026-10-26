"""Token estimation (M8/M13).

The frozen spec asks for ``cl100k_base`` and a ``len // 4`` fallback. The
fallback is deliberately simple and follows the spec literally.
"""

from __future__ import annotations

from .errors import ConfigError


class TokenCounter:
    name = "simple"

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(text) // 4


class TiktokenCounter:
    name = "tiktoken:cl100k_base"

    def __init__(self, encoding: object) -> None:
        self._encoding = encoding

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._encoding.encode(text, disallowed_special=()))  # type: ignore[attr-defined]


def build_counter() -> tuple[TokenCounter, str | None]:
    """Return (counter, warning). The warning is fixed for determinism."""

    try:
        import tiktoken  # type: ignore
    except ImportError:
        return TokenCounter(), "tiktoken 不可用，已降级为 len//4 token 估算。"
    try:
        encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:  # pragma: no cover - depends on the installed package
        return TokenCounter(), "tiktoken 不可用，已降级为 len//4 token 估算。"
    return TiktokenCounter(encoding), None


def validate_counter_name(spec: str) -> str:
    if spec not in ("auto", "simple", "tiktoken"):
        raise ConfigError(f"未知 tokenizer：{spec}")
    return spec
