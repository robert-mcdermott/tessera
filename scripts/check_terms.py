#!/usr/bin/env python3
"""Fail if committed content contains terms from a local, untracked term list.

Some words must never appear in this public repository (for example, names that only
belong in private planning notes). The list itself must not be committed either, so it
lives outside version control, by default in ``.private/terms.txt``.

Term file format: one term per line; ``#`` starts a comment. Terms match
case-insensitively at word boundaries. Prefix a term with ``=`` to match it
case-sensitively (useful for names that are also ordinary words).

Usage::

    python3 scripts/check_terms.py              # scan tracked and untracked (non-ignored) files
    python3 scripts/check_terms.py --staged     # scan staged content (pre-commit hook)
    python3 scripts/check_terms.py --message F  # scan a commit message file (commit-msg hook)
    python3 scripts/check_terms.py --install-hooks

Standard library only, so hooks run fast without a virtual environment.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_TERMS = REPO / ".private" / "terms.txt"
ENV_TERMS = "TESSERA_TERMS_FILE"
SKIP_FILES = {".gitignore"}  # must name private paths in order to ignore them
MAX_BYTES = 5_000_000
HOOK_MARKER = "tessera: private-terms check"
HOOKS = {
    "pre-commit": "exec python3 scripts/check_terms.py --staged\n",
    "commit-msg": 'exec python3 scripts/check_terms.py --message "$1"\n',
}


@dataclass(frozen=True)
class Term:
    text: str
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class Hit:
    where: str
    line: int
    term: str


def parse_terms(lines: Iterable[str]) -> list[Term]:
    terms = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        flags = re.IGNORECASE
        if line.startswith("="):
            line = line[1:].strip()
            flags = 0
        if not line:
            continue
        body = r"\s+".join(re.escape(part) for part in line.split())
        terms.append(Term(line, re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", flags)))
    return terms


def find_hits(where: str, text: str, terms: list[Term]) -> Iterator[Hit]:
    for number, line in enumerate(text.splitlines(), 1):
        for term in terms:
            if term.pattern.search(line):
                yield Hit(where, number, term.text)


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=REPO, check=True, capture_output=True, text=True
    ).stdout


def _decode(data: bytes) -> str | None:
    if b"\x00" in data[:8192] or len(data) > MAX_BYTES:
        return None  # binary or too large to be hand-written content
    return data.decode("utf-8", errors="replace")


def scan_worktree(terms: list[Term]) -> list[Hit]:
    hits: list[Hit] = []
    for name in _git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"):
        if not name or Path(name).name in SKIP_FILES:
            continue
        path = REPO / name
        if not path.is_file():
            continue
        text = _decode(path.read_bytes())
        if text is not None:
            hits += find_hits(name, text, terms)
    return hits


def scan_staged(terms: list[Term]) -> list[Hit]:
    hits: list[Hit] = []
    staged = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR").split("\0")
    for name in staged:
        if not name or Path(name).name in SKIP_FILES:
            continue
        data = subprocess.run(
            ["git", "show", f":{name}"], cwd=REPO, check=True, capture_output=True
        ).stdout
        text = _decode(data)
        if text is not None:
            hits += find_hits(name, text, terms)
    return hits


def scan_message(path: Path, terms: list[Term]) -> list[Hit]:
    lines = [ln for ln in path.read_text("utf-8").splitlines() if not ln.startswith("#")]
    return list(find_hits("commit message", "\n".join(lines), terms))


def install_hooks() -> None:
    hooks_dir = Path(_git("rev-parse", "--git-path", "hooks").strip())
    if not hooks_dir.is_absolute():
        hooks_dir = REPO / hooks_dir
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for name, command in HOOKS.items():
        hook = hooks_dir / name
        if hook.exists() and HOOK_MARKER not in hook.read_text("utf-8", errors="replace"):
            print(f"skipped {hook}: an unrelated hook already exists", file=sys.stderr)
            continue
        hook.write_text(f"#!/bin/sh\n# {HOOK_MARKER}\n{command}", encoding="utf-8")
        hook.chmod(0o755)
        print(f"installed {hook}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true", help="scan staged content only")
    mode.add_argument("--message", type=Path, metavar="FILE", help="scan a commit message file")
    mode.add_argument("--install-hooks", action="store_true", help="install git hooks")
    parser.add_argument(
        "--terms", type=Path, help=f"term file (default: ${ENV_TERMS} or .private/terms.txt)"
    )
    args = parser.parse_args(argv)

    if args.install_hooks:
        install_hooks()
        return 0

    terms_path = args.terms or Path(os.environ.get(ENV_TERMS, DEFAULT_TERMS))
    if not terms_path.is_file():
        print(f"check_terms: no term list at {terms_path}; nothing to check", file=sys.stderr)
        return 0
    terms = parse_terms(terms_path.read_text("utf-8").splitlines())

    if args.message:
        hits = scan_message(args.message, terms)
    elif args.staged:
        hits = scan_staged(terms)
    else:
        hits = scan_worktree(terms)

    for hit in hits:
        print(f"{hit.where}:{hit.line}: contains private term {hit.term!r}", file=sys.stderr)
    if hits:
        print(
            f"check_terms: {len(hits)} match(es); remove them before committing.", file=sys.stderr
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
