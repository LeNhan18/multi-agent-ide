"""Isolated draft workspace management (CLAUDE.md §4.2)."""
from __future__ import annotations

import difflib
import os
import re
import shutil
from pathlib import Path

from backend.app.core.events import AgentEventPayload, EventType
from backend.app.tools.path_guard import PathEscapeError, resolve_in_workspace

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_DRAFTS_DIR_NAME = ".agent-drafts"


def _validate_identifier(label: str, value: str) -> str:
    """Ensure agent_id/task_id cannot traverse directories."""
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a non-empty string.")
    if "/" in value or "\\" in value or ".." in value or "\x00" in value or value.startswith("."):
        raise PathEscapeError(
            f"Unsafe {label} '{value}': traversal characters ('/', '\\\\', '..') are forbidden."
        )
    if not _SAFE_ID_RE.fullmatch(value):
        raise ValueError(
            f"Invalid {label} '{value}': only alphanumeric, '.', '_', '-' characters are allowed."
        )
    return value


def _assert_no_symlinks_in_chain(base: Path, rel: Path) -> None:
    """Reject any symlink along relative components beneath base."""
    current = base
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            raise PathEscapeError(f"Symlink escape blocked at '{current}'.")


class DraftWorkspaceManager:
    """Manage per-agent, per-task isolated draft directories under .agent-drafts."""

    def __init__(self, workspace_root: str | Path) -> None:
        resolved = Path(workspace_root).resolve()
        if resolved.exists() and not resolved.is_dir():
            raise NotADirectoryError(f"workspace_root is not a directory: {resolved}")
        self.workspace_root = resolved

    @property
    def _drafts_root(self) -> Path:
        root = self.workspace_root / _DRAFTS_DIR_NAME
        if root.is_symlink() or not root.resolve().is_relative_to(self.workspace_root):
            raise PathEscapeError(
                f"Draft root '{root}' is a symlink or escapes workspace."
            )
        return root

    def draft_dir(self, agent_id: str, task_id: str) -> Path:
        """Return and validate the isolated draft directory path for (agent_id, task_id)."""
        safe_agent = _validate_identifier("agent_id", agent_id)
        safe_task = _validate_identifier("task_id", task_id)
        drafts_root = self._drafts_root
        candidate = drafts_root / f"{safe_agent}-{safe_task}"
        if candidate.is_symlink() or not candidate.resolve().is_relative_to(
            drafts_root.resolve()
        ):
            raise PathEscapeError(
                f"Draft directory '{candidate}' is a symlink or escapes '{drafts_root}'."
            )
        return candidate

    def stage_file(self, agent_id: str, task_id: str, rel_path: str) -> Path:
        """Copy an existing workspace file into the draft directory, or create an empty draft file."""
        src = resolve_in_workspace(self.workspace_root, rel_path)
        rel = src.relative_to(self.workspace_root)
        if not rel.parts or rel.parts[0] == _DRAFTS_DIR_NAME:
            raise PathEscapeError(
                f"Cannot stage workspace root or '{_DRAFTS_DIR_NAME}' path '{rel_path}'."
            )
        _assert_no_symlinks_in_chain(self.workspace_root, rel)

        ddir = self.draft_dir(agent_id, task_id)
        ddir.mkdir(parents=True, exist_ok=True)
        _assert_no_symlinks_in_chain(ddir, rel)

        dst = ddir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        _assert_no_symlinks_in_chain(ddir, rel)

        if src.exists():
            if src.is_dir():
                raise IsADirectoryError(f"Cannot stage directory '{rel_path}'.")
            if not dst.exists():
                shutil.copy2(src, dst)
        elif not dst.exists():
            dst.touch()
        return dst

    def _collect_draft_entries(self, ddir: Path) -> list[tuple[Path, Path, Path]]:
        """Collect validated (draft_file, workspace_target, rel_path) tuples."""
        if not ddir.exists():
            return []
        entries: list[tuple[Path, Path, Path]] = []
        ddir_resolved = ddir.resolve()

        for dirpath, dirnames, filenames in os.walk(ddir, followlinks=False):
            dir_path = Path(dirpath)
            for name in (*dirnames, *filenames):
                candidate = dir_path / name
                if candidate.is_symlink():
                    raise PathEscapeError(
                        f"Symlink detected inside draft directory: '{candidate}'."
                    )
            for filename in sorted(filenames):
                draft_file = dir_path / filename
                if not draft_file.resolve().is_relative_to(ddir_resolved):
                    raise PathEscapeError(
                        f"Draft file '{draft_file}' resolves outside '{ddir}'."
                    )
                rel = draft_file.relative_to(ddir)
                _assert_no_symlinks_in_chain(ddir, rel)
                _assert_no_symlinks_in_chain(self.workspace_root, rel)
                target = resolve_in_workspace(self.workspace_root, rel)
                if target.exists() and target.is_dir():
                    raise IsADirectoryError(
                        f"Workspace target '{rel.as_posix()}' is a directory."
                    )
                entries.append((draft_file, target, rel))

        entries.sort(key=lambda item: item[2].as_posix())
        return entries

    def compute_diffs(self, agent_id: str, task_id: str) -> dict[str, str]:
        """Compute unified diffs between staged/edited draft files and workspace originals."""
        ddir = self.draft_dir(agent_id, task_id)
        diffs: dict[str, str] = {}
        for draft_file, target, rel in self._collect_draft_entries(ddir):
            rel_posix = rel.as_posix()
            try:
                draft_text = draft_file.read_text(encoding="utf-8")
                orig_text = target.read_text(encoding="utf-8") if target.exists() else ""
            except UnicodeDecodeError:
                draft_bytes = draft_file.read_bytes()
                orig_bytes = target.read_bytes() if target.exists() else b""
                if draft_bytes != orig_bytes:
                    diffs[rel_posix] = (
                        f"--- a/{rel_posix}\n+++ b/{rel_posix}\nBinary files differ"
                    )
                continue

            diff_text = "\n".join(
                difflib.unified_diff(
                    orig_text.splitlines(),
                    draft_text.splitlines(),
                    fromfile=f"a/{rel_posix}",
                    tofile=f"b/{rel_posix}",
                    lineterm="",
                )
            )
            if diff_text:
                diffs[rel_posix] = diff_text
        return diffs

    def build_diff_events(self, agent_id: str, task_id: str) -> list[AgentEventPayload]:
        """Build DIFF_PROPOSED WebSocket events for all modified draft files."""
        diffs = self.compute_diffs(agent_id, task_id)
        draft_rel = (
            self.draft_dir(agent_id, task_id)
            .relative_to(self.workspace_root)
            .as_posix()
        )
        return [
            AgentEventPayload(
                event_type=EventType.DIFF_PROPOSED,
                agent_id=agent_id,
                task_id=task_id,
                payload={
                    "path": rel_path,
                    "diff": diff_text,
                    "draft_dir": draft_rel,
                },
            )
            for rel_path, diff_text in diffs.items()
        ]

    def promote_draft(self, agent_id: str, task_id: str) -> list[str]:
        """Apply approved draft files into workspace_root and remove the draft directory."""
        ddir = self.draft_dir(agent_id, task_id)
        if not ddir.exists():
            return []
        entries = self._collect_draft_entries(ddir)
        promoted: list[str] = []
        for draft_file, target, rel in entries:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(draft_file, target)
            promoted.append(rel.as_posix())
        self.discard_draft(agent_id, task_id)
        return promoted

    def discard_draft(self, agent_id: str, task_id: str) -> None:
        """Delete the isolated draft directory for (agent_id, task_id)."""
        ddir = self.draft_dir(agent_id, task_id)
        if ddir.exists():
            shutil.rmtree(ddir)
