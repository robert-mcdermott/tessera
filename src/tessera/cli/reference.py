"""Generate the command reference (docs/cli.md) from the parser itself.

A test compares the file with this output, so the reference cannot drift from the commands.
Regenerate it with ``UPDATE_DOCS=1 uv run pytest tests/test_docs.py``.
"""

from __future__ import annotations

import argparse

HEADER = """# Command reference

Every command, with its options. Run any command with `--help` for the same text. Global
options go before the command: `uv run tessera --config site.toml sync`.

This page is generated from the commands themselves.
"""


def _subparsers(parser: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser, str]]:
    found = []
    for action in parser._actions:  # argparse exposes no public way to walk subcommands
        if isinstance(action, argparse._SubParsersAction):
            helps = {a.dest: a.help or "" for a in action._choices_actions}
            for name, sub in action.choices.items():
                found.append((name, sub, helps.get(name, "")))
    return found


def _usage(parser: argparse.ArgumentParser) -> str:
    # A fixed width, so usage lines wrap the same on every machine.
    formatter = argparse.HelpFormatter(prog=parser.prog, width=100)
    formatter.add_usage(parser.usage, parser._actions, parser._mutually_exclusive_groups)
    return formatter.format_help().strip()


def _options(parser: argparse.ArgumentParser) -> list[str]:
    rows = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction | argparse._HelpAction):
            continue
        if action.option_strings:
            name = ", ".join(action.option_strings)
            if action.nargs != 0:  # flags such as --full take no value
                name += f" {action.metavar or action.dest.upper()}"
        else:
            name = action.metavar or action.dest
        text = (action.help or "").replace("|", "\\|")
        rows.append(f"| `{name}` | {text} |")
    return rows


def _section(name: str, parser: argparse.ArgumentParser, summary: str, level: int) -> list[str]:
    lines = [f"{'#' * level} `tessera {name}`", ""]
    if summary:
        lines += [summary[0].upper() + summary[1:] + ("" if summary.endswith(".") else "."), ""]
    if parser.description and parser.description != summary:
        lines += [" ".join(parser.description.split()), ""]
    lines += ["```text", _usage(parser), "```", ""]
    options = _options(parser)
    if options:
        lines += ["| Argument | Meaning |", "|---|---|", *options, ""]
    for sub_name, sub, sub_summary in _subparsers(parser):
        lines += _section(f"{name} {sub_name}", sub, sub_summary, level + 1)
    return lines


def render(parser: argparse.ArgumentParser) -> str:
    lines = [HEADER, "## Global options", "", "```text", _usage(parser), "```", ""]
    lines += ["| Argument | Meaning |", "|---|---|", *_options(parser), ""]
    for name, sub, summary in _subparsers(parser):
        lines += _section(name, sub, summary, 2)
    return "\n".join(lines).rstrip() + "\n"
