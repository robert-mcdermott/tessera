"""Exception hierarchy.

Every error Tessera raises on purpose derives from :class:`TesseraError`, so callers
(the CLI in particular) can tell expected failures from bugs.
"""

from __future__ import annotations


class TesseraError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(TesseraError):
    """The configuration file is missing, unreadable, or invalid.

    ``problems`` lists every issue found, so a user can fix them all in one pass.
    """

    def __init__(self, message: str, problems: list[str] | None = None) -> None:
        self.problems = list(problems or [])
        detail = message
        if self.problems:
            detail += "\n" + "\n".join(f"  - {p}" for p in self.problems)
        super().__init__(detail)


class DataHomeError(TesseraError):
    """The data home is missing, not writable, or not initialized."""


class LakeError(TesseraError):
    """A lake table could not be written or read."""


class LedgerError(TesseraError):
    """The ledger database is unreachable or not migrated."""
