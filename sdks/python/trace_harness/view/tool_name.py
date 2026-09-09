"""Tool-call names shared by Node Tree and AgentRun renderers."""

from __future__ import annotations

import json
import shlex
from pathlib import PurePath
from typing import Any

_SCRIPT_SUFFIXES = {
    ".py",
    ".sh",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".rb",
    ".pl",
}


def _command_name(command: Any) -> str:
    """Prefer the invoked script over shell wrappers such as cd/python/redirection."""
    if not isinstance(command, str) or not command.strip():
        return ""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()

    for token in tokens:
        candidate = token.rstrip(";&|")
        if PurePath(candidate).suffix.lower() in _SCRIPT_SUFFIXES:
            return PurePath(candidate).name

    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in {"&&", "||", ";", "|"}:
            segments.append([])
        else:
            segments[-1].append(token)
    for segment in reversed(segments):
        executable = next(
            (
                token
                for token in segment
                if not token.startswith(("-", ">", "<")) and "=" not in token
            ),
            "",
        )
        if executable and executable != "cd":
            return PurePath(executable).name
    return ""


def tool_name_detail(arguments: Any) -> str:
    """Extract the argument that best distinguishes repeated tool calls in a timeline."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (json.JSONDecodeError, TypeError):
            return ""
    if not isinstance(arguments, dict):
        return ""
    command = _command_name(arguments.get("command"))
    if command:
        return command
    for key in ("file_path", "path", "filename", "file"):
        value = arguments.get(key)
        if isinstance(value, str) and value:
            return PurePath(value).name
    return ""
