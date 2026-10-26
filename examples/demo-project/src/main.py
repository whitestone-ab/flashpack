"""Demo entry point."""

from __future__ import annotations

from models import User
from settings import Settings


def load_settings() -> Settings:
    """Load settings from the environment."""
    return Settings(timeout=5.0, retries=3)


def greet(user: User) -> str:
    """Build a greeting for a user."""
    display = user.display_name()
    return f"Hello, {display}!"


def main() -> int:
    user = User(name="Ada", role="admin")
    print(greet(user))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
