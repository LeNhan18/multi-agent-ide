"""Sandboxed file tools for agents (CLAUDE.md §4.2).

Every path is resolved inside ``workspace_root`` (normally an agent's draft). Tools never raise:
failures come back as ``ToolResult`` with an error code and a fix hint for the LLM.
"""
from __future__ import annotations

import asyncio
import difflib
import fnmatch
import os
import re
import tempfile
from pathlib import Path

from backend.app.tools.path_guard import PathEscapeError, resolve_in_workspace
from backend.app.tools.results import ToolResult

IGNORED_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".next",
        ".agent-worktrees",
        ".agent-drafts",
    }
)
PROTECTED_DIRS = frozenset({".git"})
OVERWRITE_LINE_LIMIT = 100
MAX_READ_LINES = 400
MAX_FILE_BYTES = 5 * 1024 * 1024


def _is_binary(path: Path) -> bool:
    with path.open("rb") as fh:
        return b"\0" in fh.read(8192)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _unified_diff(rel: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
        )
    )


class FileToolkit:
    """File tools bound to one workspace root (an agent's draft directory)."""

    def __init__(self, workspace_root: str | Path) -> None:
        self.root = Path(workspace_root).resolve()
        if not self.root.is_dir():
            raise NotADirectoryError(f"workspace_root does not exist: {self.root}")

    # ------------------------------------------------------------------ helpers
    def _rel(self, path: Path) -> str:
        rel = path.relative_to(self.root).as_posix()
        return rel or "."

    def _resolve(self, tool: str, target: str) -> Path | ToolResult:
        try:
            return resolve_in_workspace(self.root, target)
        except PathEscapeError as exc:
            return ToolResult.failure(
                tool, "PATH_OUTSIDE_WORKSPACE", str(exc),
                "Use a path relative to the workspace root, without '..' or absolute paths.",
                path=target,
            )

    def _protected(self, tool: str, path: Path, target: str) -> ToolResult | None:
        parts = path.relative_to(self.root).parts
        if parts and parts[0] in PROTECTED_DIRS:
            return ToolResult.failure(
                tool, "PROTECTED_PATH", f"'{target}' is inside a protected directory.",
                "Edit project files only; version-control metadata is managed by the runtime.",
                path=target,
            )
        return None

    async def _run(self, tool: str, func, *args) -> ToolResult:
        try:
            return await asyncio.to_thread(func, *args)
        except PermissionError as exc:
            return ToolResult.failure(tool, "PERMISSION_DENIED", str(exc),
                                      "Choose a file the agent is allowed to access.")
        except OSError as exc:
            return ToolResult.failure(tool, "IO_ERROR", str(exc),
                                      "Check that the path exists and is a regular file, then retry.")

    # ------------------------------------------------------------------ tools
    async def list_directory(self, path: str = ".", max_depth: int = 2, max_entries: int = 300) -> ToolResult:
        tool = "list_directory"
        resolved = self._resolve(tool, path)
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_dir():
            return ToolResult.failure(tool, "NOT_A_DIRECTORY", f"'{path}' is not a directory.",
                                      "Call list_directory('.') to see valid directories.", path=path)
        max_depth = max(1, min(max_depth, 6))

        def work() -> ToolResult:
            lines: list[str] = []
            truncated = False

            def walk(directory: Path, depth: int) -> None:
                nonlocal truncated
                entries = sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
                for entry in entries:
                    if len(lines) >= max_entries:
                        truncated = True
                        return
                    is_dir = entry.is_dir() and not entry.is_symlink()
                    if is_dir and entry.name in IGNORED_DIRS:
                        continue
                    lines.append(f"{'  ' * depth}{entry.name}{'/' if is_dir else ''}")
                    if is_dir and depth + 1 < max_depth:
                        walk(entry, depth + 1)
                        if truncated:
                            return

            walk(resolved, 0)
            content = f"{self._rel(resolved)}/\n" + ("\n".join(lines) or "(empty directory)")
            if truncated:
                content += f"\n... truncated at {max_entries} entries; list a subdirectory for more."
            return ToolResult.success(tool, content, path=self._rel(resolved),
                                      data={"entries": len(lines), "truncated": truncated})

        return await self._run(tool, work)

    async def grep_search(
        self,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        regex: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> ToolResult:
        tool = "grep_search"
        if not pattern:
            return ToolResult.failure(tool, "EMPTY_PATTERN", "pattern is empty.",
                                      "Pass the text or regular expression to search for.")
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(pattern if regex else re.escape(pattern), flags)
        except re.error as exc:
            return ToolResult.failure(tool, "INVALID_REGEX", f"Invalid regular expression: {exc}",
                                      "Fix the regex or call again with regex=false for a literal search.")
        resolved = self._resolve(tool, path)
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.exists():
            return ToolResult.failure(tool, "NOT_FOUND", f"'{path}' does not exist.",
                                      "Call list_directory to find the right path.", path=path)
        max_results = max(1, min(max_results, 500))

        def candidates():
            if resolved.is_file():
                yield resolved
                return
            for current, dirs, files in os.walk(resolved, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in IGNORED_DIRS)
                for name in sorted(files):
                    if glob and not fnmatch.fnmatch(name, glob):
                        continue
                    yield Path(current) / name

        def work() -> ToolResult:
            hits: list[str] = []
            files_with_hits = 0
            for file in candidates():
                if file.is_symlink() or file.stat().st_size > MAX_FILE_BYTES or _is_binary(file):
                    continue
                found = False
                with file.open("r", encoding="utf-8", errors="replace") as fh:
                    for number, line in enumerate(fh, 1):
                        if matcher.search(line):
                            found = True
                            hits.append(f"{self._rel(file)}:{number}: {line.rstrip()[:300]}")
                            if len(hits) >= max_results:
                                break
                files_with_hits += found
                if len(hits) >= max_results:
                    break
            if not hits:
                return ToolResult.success(tool, f"No matches for {pattern!r}.",
                                          data={"matches": 0, "files": 0})
            content = "\n".join(hits)
            if len(hits) >= max_results:
                content += f"\n... stopped at {max_results} matches; narrow the path or glob."
            return ToolResult.success(tool, content, data={"matches": len(hits), "files": files_with_hits})

        return await self._run(tool, work)

    async def read_file_lines(self, path: str, start_line: int = 1, end_line: int | None = None) -> ToolResult:
        tool = "read_file_lines"
        resolved = self._resolve(tool, path)
        if isinstance(resolved, ToolResult):
            return resolved
        if not resolved.is_file():
            return ToolResult.failure(tool, "NOT_FOUND", f"File '{path}' does not exist.",
                                      "Use list_directory or grep_search to locate the file.", path=path)

        def work() -> ToolResult:
            if resolved.stat().st_size > MAX_FILE_BYTES:
                return ToolResult.failure(tool, "FILE_TOO_LARGE", f"'{path}' is larger than 5 MB.",
                                          "Use grep_search to find the relevant lines instead.", path=path)
            if _is_binary(resolved):
                return ToolResult.failure(tool, "BINARY_FILE", f"'{path}' is a binary file.",
                                          "Use a format-specific tool (e.g. Docling for PDF/DOCX).", path=path)
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
            total = len(lines)
            first = max(1, start_line)
            if total and first > total:
                return ToolResult.failure(tool, "LINE_OUT_OF_RANGE",
                                          f"start_line {first} is past the end of the file ({total} lines).",
                                          f"Use a start_line between 1 and {total}.", path=path)
            last = total if end_line is None else min(end_line, total)
            if last < first and total:
                return ToolResult.failure(tool, "INVALID_RANGE", f"end_line {end_line} is before start_line {first}.",
                                          "Pass end_line >= start_line, or omit end_line.", path=path)
            truncated = last - first + 1 > MAX_READ_LINES
            if truncated:
                last = first + MAX_READ_LINES - 1
            width = len(str(last)) if total else 1
            body = "\n".join(f"{n:>{width}}| {lines[n - 1]}" for n in range(first, last + 1)) if total else "(empty file)"
            header = f"{self._rel(resolved)} (lines {first}-{last} of {total})"
            if truncated:
                header += f" — truncated; call again with start_line={last + 1}"
            return ToolResult.success(tool, f"{header}\n{body}", path=self._rel(resolved),
                                      data={"total_lines": total, "start_line": first, "end_line": last})

        return await self._run(tool, work)

    async def apply_search_replace_diff(
        self, path: str, search: str, replace: str, expected_occurrences: int = 1
    ) -> ToolResult:
        tool = "apply_search_replace_diff"
        resolved = self._resolve(tool, path)
        if isinstance(resolved, ToolResult):
            return resolved
        if blocked := self._protected(tool, resolved, path):
            return blocked
        if not search:
            return ToolResult.failure(tool, "EMPTY_SEARCH", "search block is empty.",
                                      "Copy the exact lines to change into search; use write_file for new files.",
                                      path=path)
        if not resolved.is_file():
            return ToolResult.failure(tool, "NOT_FOUND", f"File '{path}' does not exist.",
                                      "Create new files with write_file, or check the path with list_directory.",
                                      path=path)

        def work() -> ToolResult:
            if _is_binary(resolved):
                return ToolResult.failure(tool, "BINARY_FILE", f"'{path}' is a binary file.",
                                          "Search/replace works on text files only.", path=path)
            raw = resolved.read_bytes()
            try:
                before = raw.decode("utf-8")
            except UnicodeDecodeError:
                return ToolResult.failure(tool, "NOT_UTF8", f"'{path}' is not valid UTF-8 text.",
                                          "Convert the file to UTF-8 or edit it with a format-specific tool.",
                                          path=path)
            newline = "\r\n" if "\r\n" in before else "\n"
            norm_search = search.replace("\r\n", "\n").replace("\n", newline)
            norm_replace = replace.replace("\r\n", "\n").replace("\n", newline)
            count = before.count(norm_search)
            rel = self._rel(resolved)
            if count == 0:
                return ToolResult.failure(tool, "SEARCH_NOT_FOUND",
                                          f"The search block was not found in '{rel}'.",
                                          self._closest_hint(before, search), path=rel)
            if count != expected_occurrences:
                return ToolResult.failure(
                    tool, "AMBIGUOUS_MATCH",
                    f"The search block occurs {count} times in '{rel}', expected {expected_occurrences}.",
                    "Add surrounding lines to search so it is unique, or set expected_occurrences to the real count.",
                    path=rel,
                )
            after = before.replace(norm_search, norm_replace)
            if after == before:
                return ToolResult.failure(tool, "NO_CHANGE", "search and replace are identical.",
                                          "Put the new text in replace.", path=rel)
            _atomic_write(resolved, after)
            diff = _unified_diff(rel, before, after)
            added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
            return ToolResult.success(tool, f"Edited {rel}: +{added} -{removed} lines.", path=rel, diff=diff,
                                      data={"lines_added": added, "lines_removed": removed})

        return await self._run(tool, work)

    async def write_file(self, path: str, content: str, overwrite: bool = False) -> ToolResult:
        tool = "write_file"
        resolved = self._resolve(tool, path)
        if isinstance(resolved, ToolResult):
            return resolved
        if blocked := self._protected(tool, resolved, path):
            return blocked
        if resolved.is_dir():
            return ToolResult.failure(tool, "IS_A_DIRECTORY", f"'{path}' is a directory.",
                                      "Pass a file path.", path=path)

        def work() -> ToolResult:
            rel = self._rel(resolved)
            before = ""
            if resolved.exists():
                if not overwrite:
                    return ToolResult.failure(tool, "FILE_EXISTS", f"'{rel}' already exists.",
                                              "Edit it with apply_search_replace_diff, or pass overwrite=true for a small file.",
                                              path=rel)
                if _is_binary(resolved):
                    return ToolResult.failure(tool, "BINARY_FILE", f"'{rel}' is a binary file.",
                                              "Do not overwrite binary files with text.", path=rel)
                before = resolved.read_text(encoding="utf-8", errors="replace")
                if len(before.splitlines()) > OVERWRITE_LINE_LIMIT:
                    return ToolResult.failure(
                        tool, "OVERWRITE_TOO_LARGE",
                        f"'{rel}' has more than {OVERWRITE_LINE_LIMIT} lines and cannot be overwritten.",
                        "Use apply_search_replace_diff to change only the needed block.", path=rel,
                    )
            _atomic_write(resolved, content)
            diff = _unified_diff(rel, before, content)
            verb = "Overwrote" if before else "Created"
            return ToolResult.success(tool, f"{verb} {rel} ({len(content.splitlines())} lines).", path=rel,
                                      diff=diff, data={"created": not before})

        return await self._run(tool, work)

    # ------------------------------------------------------------------ hints
    @staticmethod
    def _closest_hint(text: str, search: str) -> str:
        target = next((l.strip() for l in search.splitlines() if l.strip()), "")
        lines = text.splitlines()
        stripped = [l.strip() for l in lines]
        match = difflib.get_close_matches(target, stripped, n=1, cutoff=0.6) if target else []
        base = "Copy the search block exactly from read_file_lines output (without the line-number prefix), including indentation."
        if match:
            number = stripped.index(match[0]) + 1
            return f"{base} The closest line is {number}: {lines[number - 1].strip()!r}. Re-read around line {number}."
        return f"{base} Call read_file_lines to see the current content."
