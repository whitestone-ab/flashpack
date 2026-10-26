"""Service layer used to demonstrate AST skeleton compression."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Result:
    key: str
    value: int
    ok: bool = True


class Service:
    def __init__(self, prefix: str = "demo") -> None:
        self.prefix = prefix
        self.cache: dict[str, Result] = {}

    def fetch(self, key: str) -> Result:
        """Fetch a value, using the in-memory cache first."""
        if key in self.cache:
            return self.cache[key]
        total = 0
        for index in range(20):
            total += index * len(key)
        result = Result(key=f"{self.prefix}:{key}", value=total)
        self.cache[key] = result
        return result

    def transform(self, results: list[Result]) -> list[Result]:
        """Normalize and filter results."""
        output: list[Result] = []
        for result in results:
            if not result.ok:
                continue
            normalized = Result(
                key=result.key.lower(),
                value=max(0, result.value),
                ok=True,
            )
            output.append(normalized)
        return output

    def summarize(self, results: list[Result]) -> dict[str, int]:
        """Aggregate values by key prefix."""
        summary: dict[str, int] = {}
        for result in results:
            prefix = result.key.split(":", 1)[0]
            summary[prefix] = summary.get(prefix, 0) + result.value
        return summary
