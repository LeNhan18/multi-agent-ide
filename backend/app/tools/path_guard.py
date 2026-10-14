"""Path sandboxing (CLAUDE.md §4.1)."""
from __future__ import annotations

from pathlib import Path


class PathEscapeError(ValueError):
    """Raised when a path resolves outside the allowed workspace root."""


def resolve_in_workspace(workspace_root: str | Path, target: str | Path) -> Path:
    root = Path(workspace_root).resolve()
    candidate = Path(target)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root):
        raise PathEscapeError(
            f"Path '{target}' resolves outside the workspace. "
            f"Use a path relative to the workspace root."
        )
    return resolved
