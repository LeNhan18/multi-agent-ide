"""Draft isolation for agents (CLAUDE.md §4.2).

Each agent task works in its own draft and never touches the main workspace directly:

* Git repository root  -> ``git worktree`` at ``.agent-worktrees/<agent>-<task>`` on branch
  ``agent/<agent>/<task>``; promotion is a ``--no-ff`` merge.
* Plain folder         -> snapshot copy at ``.agent-drafts/<agent>-<task>``; promotion copies the
  changed files back after checking that the originals did not change since the snapshot.

``promote`` requires an ``Approval`` carrying both the reviewer's APPROVE verdict and the
human approver, so the merge step cannot run on an agent's word alone.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import os
import re
import shutil
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from backend.app.tools.files import IGNORED_DIRS, MAX_FILE_BYTES

WORKTREE_DIR = ".agent-worktrees"
SNAPSHOT_DIR = ".agent-drafts"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
AGENT_EMAIL = "agents@ai-office.local"


class DraftError(Exception):
    """Base error for draft operations."""


class DraftConflictError(DraftError):
    """The main workspace changed in a way that conflicts with the draft."""

    def __init__(self, message: str, files: list[str]) -> None:
        super().__init__(message)
        self.files = files


class DraftKind(str, Enum):
    GIT_WORKTREE = "git_worktree"
    SNAPSHOT = "snapshot"


class Draft(BaseModel):
    agent_id: str
    task_id: str
    kind: DraftKind
    path: str
    branch: str | None = None
    base_ref: str | None = None
    snapshot: dict[str, str] = Field(default_factory=dict)


class ChangeSet(BaseModel):
    added: list[str] = Field(default_factory=list)
    modified: list[str] = Field(default_factory=list)
    deleted: list[str] = Field(default_factory=list)
    diff: str = ""

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.modified or self.deleted)

    @property
    def files(self) -> list[str]:
        return sorted({*self.added, *self.modified, *self.deleted})


class Approval(BaseModel):
    reviewer_role: str
    reviewer_verdict: Literal["APPROVE", "REJECT"]
    approved_by: str

    @field_validator("approved_by", "reviewer_role")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


def _check_id(kind: str, value: str) -> str:
    if not _ID.fullmatch(value):
        raise DraftError(f"{kind} must match {_ID.pattern}: {value!r}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _walk_files(root: Path) -> dict[str, Path]:
    """Regular files under root (symlinks and ignored dirs skipped), keyed by posix relative path."""
    found: dict[str, Path] = {}
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not (Path(current) / d).is_symlink()]
        for name in files:
            file = Path(current) / name
            if file.is_symlink() or not file.is_file():
                continue
            found[file.relative_to(root).as_posix()] = file
    return found


def _text_diff(rel: str, before: Path | None, after: Path | None) -> str:
    def load(path: Path | None) -> list[str] | None:
        if path is None:
            return []
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            return None
        return data.decode("utf-8", errors="replace").splitlines(keepends=True)

    old, new = load(before), load(after)
    if old is None or new is None:
        return f"Binary or large file {rel} changed\n"
    return "".join(difflib.unified_diff(old, new, fromfile=f"a/{rel}", tofile=f"b/{rel}"))


class DraftManager:
    """Creates, inspects, promotes and discards agent drafts for one workspace."""

    def __init__(self, workspace_root: str | Path, max_snapshot_bytes: int = 500 * 1024 * 1024) -> None:
        self.root = Path(workspace_root).resolve()
        if not self.root.is_dir():
            raise DraftError(f"workspace_root does not exist: {self.root}")
        self.max_snapshot_bytes = max_snapshot_bytes

    # ------------------------------------------------------------------ git
    async def _git(self, cwd: Path, *args: str, check: bool = True) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", "-c", "core.quotepath=off", *args,
            cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"},
        )
        out, err = await proc.communicate()
        result = (proc.returncode or 0, out.decode("utf-8", "replace"), err.decode("utf-8", "replace"))
        if check and result[0] != 0:
            raise DraftError(f"git {' '.join(args)} failed: {result[2].strip() or result[1].strip()}")
        return result

    async def is_git_repo(self) -> bool:
        if shutil.which("git") is None:
            return False
        code, top, _ = await self._git(self.root, "rev-parse", "--show-toplevel", check=False)
        if code != 0 or Path(top.strip()).resolve() != self.root:
            return False
        code, _, _ = await self._git(self.root, "rev-parse", "--verify", "HEAD", check=False)
        return code == 0

    async def _exclude_draft_dirs(self) -> None:
        _, out, _ = await self._git(self.root, "rev-parse", "--git-path", "info/exclude")
        exclude = Path(out.strip())
        if not exclude.is_absolute():
            exclude = self.root / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        current = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        missing = [p for p in (f"/{WORKTREE_DIR}/", f"/{SNAPSHOT_DIR}/") if p not in current.splitlines()]
        if missing:
            prefix = "" if not current or current.endswith("\n") else "\n"
            exclude.write_text(current + prefix + "\n".join(missing) + "\n", encoding="utf-8")

    # ------------------------------------------------------------------ lifecycle
    async def create(self, agent_id: str, task_id: str) -> Draft:
        _check_id("agent_id", agent_id)
        _check_id("task_id", task_id)
        name = f"{agent_id}-{task_id}"
        if await self.is_git_repo():
            await self._exclude_draft_dirs()
            path = self.root / WORKTREE_DIR / name
            branch = f"agent/{agent_id}/{task_id}"
            if path.exists():
                raise DraftError(f"Draft already exists: {path}")
            _, head, _ = await self._git(self.root, "rev-parse", "HEAD")
            await self._git(self.root, "worktree", "add", "-b", branch, str(path), head.strip())
            return Draft(agent_id=agent_id, task_id=task_id, kind=DraftKind.GIT_WORKTREE,
                         path=str(path), branch=branch, base_ref=head.strip())
        path = self.root / SNAPSHOT_DIR / name
        if path.exists():
            raise DraftError(f"Draft already exists: {path}")
        snapshot = await asyncio.to_thread(self._copy_snapshot, path)
        return Draft(agent_id=agent_id, task_id=task_id, kind=DraftKind.SNAPSHOT,
                     path=str(path), snapshot=snapshot)

    def _copy_snapshot(self, target: Path) -> dict[str, str]:
        files = _walk_files(self.root)
        total = sum(f.stat().st_size for f in files.values())
        if total > self.max_snapshot_bytes:
            raise DraftError(
                f"Workspace is {total // (1024 * 1024)} MB, above the snapshot limit; "
                "use a Git repository or a smaller folder."
            )
        manifest: dict[str, str] = {}
        target.mkdir(parents=True)
        try:
            for rel, src in files.items():
                dst = target / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                manifest[rel] = _sha256(src)
        except BaseException:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return manifest

    async def changes(self, draft: Draft) -> ChangeSet:
        path = Path(draft.path)
        if not path.is_dir():
            raise DraftError(f"Draft directory is missing: {path}")
        if draft.kind is DraftKind.GIT_WORKTREE:
            await self._git(path, "add", "-A")
            _, status, _ = await self._git(path, "diff", "--cached", "--name-status", "--no-renames", draft.base_ref or "HEAD")
            change = ChangeSet()
            for line in status.splitlines():
                code, _, rel = line.partition("\t")
                {"A": change.added, "D": change.deleted}.get(code[:1], change.modified).append(rel)
            _, change.diff, _ = await self._git(path, "diff", "--cached", "--no-renames", draft.base_ref or "HEAD")
            return change
        return await asyncio.to_thread(self._snapshot_changes, draft)

    def _snapshot_changes(self, draft: Draft) -> ChangeSet:
        current = _walk_files(Path(draft.path))
        change = ChangeSet()
        parts: list[str] = []
        originals = self.root
        for rel in sorted(set(current) | set(draft.snapshot)):
            if rel not in draft.snapshot:
                change.added.append(rel)
                parts.append(_text_diff(rel, None, current[rel]))
            elif rel not in current:
                change.deleted.append(rel)
                original = originals / rel
                parts.append(_text_diff(rel, original if original.is_file() else None, None))
            elif _sha256(current[rel]) != draft.snapshot[rel]:
                change.modified.append(rel)
                original = originals / rel
                parts.append(_text_diff(rel, original if original.is_file() else None, current[rel]))
        change.diff = "".join(parts)
        return change

    async def promote(self, draft: Draft, approval: Approval, message: str | None = None) -> ChangeSet:
        """Apply an approved draft to the main workspace, then remove the draft."""
        if approval.reviewer_verdict != "APPROVE":
            raise DraftError("Draft can only be promoted after the reviewer verdict is APPROVE.")
        change = await self.changes(draft)
        if change.is_empty:
            await self.discard(draft)
            return change
        if draft.kind is DraftKind.GIT_WORKTREE:
            await self._promote_git(draft, approval, message)
        else:
            await asyncio.to_thread(self._promote_snapshot, draft, change)
        await self.discard(draft)
        return change

    async def _promote_git(self, draft: Draft, approval: Approval, message: str | None) -> None:
        path = Path(draft.path)
        title = message or f"{draft.agent_id}: {draft.task_id}"
        body = f"Reviewed-by: {approval.reviewer_role}\nApproved-by: {approval.approved_by}"
        identity = ["-c", f"user.name=AI Office ({draft.agent_id})", "-c", f"user.email={AGENT_EMAIL}"]
        await self._git(path, *identity, "commit", "-m", title, "-m", body)
        code, _, err = await self._git(
            self.root, *identity, "merge", "--no-ff", "--no-edit", draft.branch or "", check=False
        )
        if code != 0:
            _, conflicted, _ = await self._git(self.root, "diff", "--name-only", "--diff-filter=U", check=False)
            await self._git(self.root, "merge", "--abort", check=False)
            raise DraftConflictError(
                f"Merging {draft.branch} failed: {err.strip()}",
                conflicted.split() or [],
            )

    def _promote_snapshot(self, draft: Draft, change: ChangeSet) -> None:
        draft_root = Path(draft.path)
        conflicts: list[str] = []
        for rel in change.files:
            original = self.root / rel
            expected = draft.snapshot.get(rel)
            if expected is None:
                if original.exists():
                    conflicts.append(rel)
            elif not original.is_file() or _sha256(original) != expected:
                conflicts.append(rel)
        if conflicts:
            raise DraftConflictError("Files changed in the main workspace since the draft was created.", conflicts)
        for rel in [*change.added, *change.modified]:
            src = draft_root / rel
            dst = self.root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.with_name(f".{dst.name}.promote.tmp")
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
        for rel in change.deleted:
            (self.root / rel).unlink(missing_ok=True)

    async def discard(self, draft: Draft) -> None:
        path = Path(draft.path)
        if draft.kind is DraftKind.GIT_WORKTREE:
            if path.exists():
                await self._git(self.root, "worktree", "remove", "--force", str(path))
            await self._git(self.root, "worktree", "prune", check=False)
            if draft.branch:
                await self._git(self.root, "branch", "-D", draft.branch, check=False)
        else:
            await asyncio.to_thread(shutil.rmtree, path, True)
