import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.app.tools.files import FileToolkit
from backend.app.tools.isolation import (
    Approval,
    DraftConflictError,
    DraftError,
    DraftKind,
    DraftManager,
)

APPROVED = Approval(reviewer_role="reviewer", reviewer_verdict="APPROVE", approved_by="le")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "report.md").write_text("# Report\nRevenue: 10\n")
    (root / "old.md").write_text("obsolete\n")
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git not installed")
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("print('v1')\n")
    git(root, "add", ".")
    git(root, "commit", "-q", "-m", "init")
    return root


async def test_snapshot_draft_isolates_and_promotes(folder):
    manager = DraftManager(folder)
    draft = await manager.create("writer", "t1")
    assert draft.kind is DraftKind.SNAPSHOT and Path(draft.path).is_relative_to(folder / ".agent-drafts")

    tk = FileToolkit(draft.path)
    assert (await tk.apply_search_replace_diff("report.md", "Revenue: 10", "Revenue: 12")).ok
    assert (await tk.write_file("summary.md", "ok\n")).ok
    (Path(draft.path) / "old.md").unlink()
    assert "Revenue: 10" in (folder / "report.md").read_text()

    change = await manager.changes(draft)
    assert (change.added, change.modified, change.deleted) == (["summary.md"], ["report.md"], ["old.md"])
    assert "+Revenue: 12" in change.diff

    await manager.promote(draft, APPROVED)
    assert "Revenue: 12" in (folder / "report.md").read_text()
    assert (folder / "summary.md").exists() and not (folder / "old.md").exists()
    assert not Path(draft.path).exists()


async def test_snapshot_conflict_blocks_promotion(folder):
    manager = DraftManager(folder)
    draft = await manager.create("writer", "t2")
    (Path(draft.path) / "report.md").write_text("draft edit\n")
    (folder / "report.md").write_text("human edit\n")
    with pytest.raises(DraftConflictError) as exc:
        await manager.promote(draft, APPROVED)
    assert exc.value.files == ["report.md"]
    assert (folder / "report.md").read_text() == "human edit\n"


async def test_rejected_or_unsigned_approval_cannot_promote(folder):
    manager = DraftManager(folder)
    draft = await manager.create("writer", "t3")
    (Path(draft.path) / "report.md").write_text("x\n")
    with pytest.raises(DraftError):
        await manager.promote(draft, Approval(reviewer_role="reviewer", reviewer_verdict="REJECT", approved_by="le"))
    with pytest.raises(ValidationError):
        Approval(reviewer_role="reviewer", reviewer_verdict="APPROVE", approved_by="  ")
    assert "Revenue: 10" in (folder / "report.md").read_text()


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "-rf", "x" * 65])
async def test_ids_validated(folder, bad):
    with pytest.raises(DraftError):
        await DraftManager(folder).create(bad, "t1")


async def test_snapshot_size_limit(folder):
    with pytest.raises(DraftError):
        await DraftManager(folder, max_snapshot_bytes=5).create("writer", "big")
    assert not (folder / ".agent-drafts" / "writer-big").exists()


async def test_git_worktree_draft_merges_after_approval(repo):
    manager = DraftManager(repo)
    draft = await manager.create("coder", "t1")
    assert draft.kind is DraftKind.GIT_WORKTREE and draft.branch == "agent/coder/t1"
    assert ".agent-worktrees" not in git(repo, "status", "--porcelain")

    tk = FileToolkit(draft.path)
    assert (await tk.apply_search_replace_diff("app.py", "v1", "v2")).ok
    assert (await tk.write_file("util.py", "X = 1\n")).ok
    assert (repo / "app.py").read_text() == "print('v1')\n"

    change = await manager.changes(draft)
    assert change.modified == ["app.py"] and change.added == ["util.py"]

    await manager.promote(draft, APPROVED, message="coder: bump version")
    assert (repo / "app.py").read_text() == "print('v2')\n"
    log = git(repo, "log", "--format=%s%n%b", "-3")
    assert "coder: bump version" in log and "Approved-by: le" in log
    assert not Path(draft.path).exists()
    assert "agent/coder/t1" not in git(repo, "branch", "--list")


async def test_git_conflict_aborts_merge(repo):
    manager = DraftManager(repo)
    draft = await manager.create("coder", "t2")
    (Path(draft.path) / "app.py").write_text("print('agent')\n")
    (repo / "app.py").write_text("print('human')\n")
    git(repo, "commit", "-qam", "human change")
    with pytest.raises(DraftConflictError) as exc:
        await manager.promote(draft, APPROVED)
    assert exc.value.files == ["app.py"]
    assert git(repo, "status", "--porcelain").strip() == ""
    assert (repo / "app.py").read_text() == "print('human')\n"
    await manager.discard(draft)


async def test_folder_inside_repo_uses_snapshot(repo):
    sub = repo / "sub"
    sub.mkdir()
    (sub / "a.txt").write_text("a\n")
    draft = await DraftManager(sub).create("writer", "t1")
    assert draft.kind is DraftKind.SNAPSHOT
