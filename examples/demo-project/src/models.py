"""Domain models."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class User:
    name: str
    role: str = "member"

    def display_name(self) -> str:
        return self.name.title()

    def is_admin(self) -> bool:
        return self.role == "admin"
