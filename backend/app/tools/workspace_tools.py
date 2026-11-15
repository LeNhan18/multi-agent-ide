"""Self-healing workspace file tools with path sandboxing (CLAUDE.md §4.2, §4.5)."""
from __future__ import annotations

import difflib
import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from backend.app.tools.path_guard import PathEscapeError, resolve_in_workspace

_GREP_IGNORED_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", ".agent-drafts"}
)


class ToolExecutionResult(BaseModel):
    """Structured outcome returned by every workspace tool."""

    ok: bool
    tool_name: str
    output: Any = None
    diff: str | None = None
    error_hint: str | None = None


def _unified_diff(original: str, updated: str, rel_display: str) -> str:
    """Return a unified diff between two text snapshots."""
    return "\n".join(
        difflib.unified_diff(
            original.splitlines(),
            updated.splitlines(),
            fromfile=f"a/{rel_display}",
            tofile=f"b/{rel_display}",
            lineterm="",
        )
    )


def _error_result(tool_name: str, rel_path: str, exc: Exception) -> ToolExecutionResult:
    """Map filesystem and validation errors into self-healing agent hints."""
    if isinstance(exc, PathEscapeError):
        hint = str(exc)
    elif isinstance(exc, FileNotFoundError):
        hint = (
            f"Path '{rel_path}' does not exist. "
            "Call list_directory to inspect available workspace files."
        )
    elif isinstance(exc, IsADirectoryError):
        hint = f"'{rel_path}' is a directory, not a file. Use list_directory instead."
    elif isinstance(exc, NotADirectoryError):
        hint = f"'{rel_path}' is not a directory. Use read_file_lines to inspect a file."
    elif isinstance(exc, UnicodeDecodeError):
        hint = (
            f"File '{rel_path}' is not valid UTF-8 text (possibly binary). "
            "Use a specialized document parser instead."
        )
    elif isinstance(exc, re.error):
        hint = (
            f"Invalid regular expression: {exc}. "
            "Escape special characters or simplify the search pattern."
        )
    else:
        hint = str(exc)
    return ToolExecutionResult(ok=False, tool_name=tool_name, error_hint=hint)


def _run_tool(
    tool_name: str,
    rel_path: str,
    action: Callable[[], ToolExecutionResult],
) -> ToolExecutionResult:
    try:
        return action()
    except (PathEscapeError, OSError, UnicodeDecodeError, ValueError, re.error) as exc:
        return _error_result(tool_name, rel_path, exc)


def list_directory(
    workspace_root: str | Path,
    rel_path: str = ".",
) -> ToolExecutionResult:
    """List files and subdirectories inside a workspace directory."""

    def _exec() -> ToolExecutionResult:
        root = Path(workspace_root).resolve()
        target = resolve_in_workspace(root, rel_path)
        if not target.exists():
            raise FileNotFoundError(rel_path)
        if not target.is_dir():
            raise NotADirectoryError(rel_path)

        entries: list[str] = []
        for entry in target.iterdir():
            if entry.is_symlink():
                try:
                    resolved = entry.resolve()
                except OSError:
                    continue
                if not resolved.is_relative_to(root):
                    continue
            entries.append(f"{entry.name}/" if entry.is_dir() else entry.name)
        entries.sort(key=lambda name: (not name.endswith("/"), name.casefold()))
        return ToolExecutionResult(ok=True, tool_name="list_directory", output=entries)

    return _run_tool("list_directory", rel_path, _exec)


def read_file_lines(
    workspace_root: str | Path,
    rel_path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> ToolExecutionResult:
    """Read an inclusive 1-based line range from a UTF-8 workspace file."""

    def _exec() -> ToolExecutionResult:
        if start_line < 1 or end_line < start_line:
            raise ValueError(
                f"Invalid line range [{start_line}, {end_line}]; "
                "require 1 <= start_line <= end_line."
            )
        target = resolve_in_workspace(workspace_root, rel_path)
        if not target.exists():
            raise FileNotFoundError(rel_path)
        if target.is_dir():
            raise IsADirectoryError(rel_path)

        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[start_line - 1 : end_line]
        numbered = "\n".join(
            f"{lineno}: {line}" for lineno, line in enumerate(selected, start=start_line)
        )
        return ToolExecutionResult(
            ok=True,
            tool_name="read_file_lines",
            output=numbered,
        )

    return _run_tool("read_file_lines", rel_path, _exec)


def _search_single_file(
    file_path: Path,
    display_path: str,
    regex: re.Pattern[str],
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for lineno, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if regex.search(line):
            matches.append({"file": display_path, "line": lineno, "text": line})
    return matches


def grep_search(
    workspace_root: str | Path,
    pattern: str,
    rel_path: str = ".",
) -> ToolExecutionResult:
    """Search for a regex pattern in a workspace file or directory tree."""

    def _exec() -> ToolExecutionResult:
        if not pattern:
            raise ValueError("Search pattern must not be empty.")
        regex = re.compile(pattern)
        root = Path(workspace_root).resolve()
        target = resolve_in_workspace(root, rel_path)
        if not target.exists():
            raise FileNotFoundError(rel_path)

        if target.is_file():
            matches = _search_single_file(
                target, target.relative_to(root).as_posix(), regex
            )
            return ToolExecutionResult(ok=True, tool_name="grep_search", output=matches)

        matches: list[dict[str, Any]] = []
        for dirpath, subdirs, filenames in os.walk(target, followlinks=False):
            subdirs[:] = sorted(
                d
                for d in subdirs
                if d not in _GREP_IGNORED_DIRS and not (Path(dirpath) / d).is_symlink()
            )
            for filename in sorted(filenames):
                candidate = Path(dirpath) / filename
                if candidate.is_symlink():
                    try:
                        resolved = candidate.resolve()
                    except OSError:
                        continue
                    if not resolved.is_relative_to(root):
                        continue
                try:
                    display = candidate.relative_to(root).as_posix()
                    matches.extend(_search_single_file(candidate, display, regex))
                except (UnicodeDecodeError, OSError):
                    continue
        return ToolExecutionResult(ok=True, tool_name="grep_search", output=matches)

    return _run_tool("grep_search", rel_path, _exec)


def apply_search_replace_diff(
    workspace_root: str | Path,
    rel_path: str,
    search_block: str,
    replace_block: str,
) -> ToolExecutionResult:
    """Surgically replace a unique block in a workspace file and return a unified diff."""

    def _exec() -> ToolExecutionResult:
        if not search_block:
            raise ValueError(
                "search_block must not be empty. Provide the exact block to replace."
            )
        root = Path(workspace_root).resolve()
        target = resolve_in_workspace(root, rel_path)
        if not target.exists():
            raise FileNotFoundError(rel_path)
        if target.is_dir():
            raise IsADirectoryError(rel_path)

        original = target.read_text(encoding="utf-8")
        normalized_search = search_block.replace("\r\n", "\n")
        normalized_replace = replace_block.replace("\r\n", "\n")
        occurrences = original.count(normalized_search)
        if occurrences == 0:
            raise ValueError(
                f"search_block not found in '{rel_path}'. "
                "Call read_file_lines to verify exact text, indentation, and line breaks."
            )
        if occurrences > 1:
            raise ValueError(
                f"search_block matched {occurrences} times in '{rel_path}'. "
                "Include more surrounding context so it matches exactly once."
            )

        updated = original.replace(normalized_search, normalized_replace, 1)
        rel_display = target.relative_to(root).as_posix()
        diff_text = _unified_diff(original, updated, rel_display)
        target.write_text(updated, encoding="utf-8")
        return ToolExecutionResult(
            ok=True,
            tool_name="apply_search_replace_diff",
            output=f"Applied diff to {rel_display}",
            diff=diff_text,
        )

    return _run_tool("apply_search_replace_diff", rel_path, _exec)


def write_note(
    workspace_root: str | Path,
    rel_path: str,
    content: str,
) -> ToolExecutionResult:
    """Create or overwrite a text/Markdown note inside the workspace."""

    def _exec() -> ToolExecutionResult:
        root = Path(workspace_root).resolve()
        target = resolve_in_workspace(root, rel_path)
        if target.exists() and target.is_dir():
            raise IsADirectoryError(rel_path)

        rel_display = target.relative_to(root).as_posix()
        previous = ""
        can_diff = True
        if target.exists():
            try:
                previous = target.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                can_diff = False

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        diff_text = _unified_diff(previous, content, rel_display) if can_diff else None
        return ToolExecutionResult(
            ok=True,
            tool_name="write_note",
            output=f"Wrote {len(content)} characters to {rel_display}",
            diff=diff_text,
        )

    return _run_tool("write_note", rel_path, _exec)
