"""User-facing exception types and exit-code mapping (M17)."""

from __future__ import annotations


class FlashpackError(Exception):
    """Base class for expected failures."""


class ParameterError(FlashpackError):
    """Invalid command-line parameters or a conflict (exit code 1)."""


class TargetError(FlashpackError):
    """Missing/invalid target directory (exit code 2)."""


class OutputError(FlashpackError):
    """The output file cannot be written (exit code 3)."""


class ConfigError(FlashpackError):
    """Internal configuration problem."""
