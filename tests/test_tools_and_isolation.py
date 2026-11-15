"""Comprehensive synchronous unit tests for workspace tools, draft isolation, and sandbox/watcher."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from docker.errors import ContainerError, DockerException
from watchdog.events import (
    DirCreatedEvent,
    DirModifiedEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)

from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs.state import ResourceProfile
from backend.app.sandbox import (
    DockerSandboxManager,
    SandboxRunResult,
    WorkspaceFileEventHandler,
)
from backend.app.tools import (
    DraftWorkspaceManager,
    PathEscapeError,
    ToolExecutionResult,
    apply_search_replace_diff,
    grep_search,
    list_directory,
    read_file_lines,
    write_note,
)


class WorkspaceToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "workspace"
        self.outside = self.root / "outside"
        self.ws.mkdir()
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("TOP_SECRET", encoding="utf-8")

        (self.ws / "src").mkdir()
        (self.ws / "src" / "app.py").write_text(
            "def greet(name):\n    return f'Hello, {name}'\n\ndef bye():\n    return 'bye'\n",
            encoding="utf-8",
        )
        (self.ws / "notes.md").write_text("alpha\nbeta\ngamma", encoding="utf-8")
        (self.ws / "binary.bin").write_bytes(b"\xff\xfe\x00\x80\x81")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_list_directory_and_self_healing(self) -> None:
        res = list_directory(self.ws, ".")
        self.assertIsInstance(res, ToolExecutionResult)
        self.assertTrue(res.ok)
        self.assertEqual(res.tool_name, "list_directory")
        self.assertEqual(res.output, ["src/", "binary.bin", "notes.md"])

        sub = list_directory(self.ws, "src")
        self.assertTrue(sub.ok)
        self.assertEqual(sub.output, ["app.py"])

        missing = list_directory(self.ws, "missing_dir")
        self.assertFalse(missing.ok)
        self.assertIn("list_directory", missing.error_hint or "")

        not_dir = list_directory(self.ws, "notes.md")
        self.assertFalse(not_dir.ok)
        self.assertIn("not a directory", not_dir.error_hint or "")

        escape = list_directory(self.ws, "../outside")
        self.assertFalse(escape.ok)
        self.assertIn("outside the workspace", escape.error_hint or "")

    def test_read_file_lines_and_self_healing(self) -> None:
        res = read_file_lines(self.ws, "src/app.py", start_line=1, end_line=2)
        self.assertTrue(res.ok)
        self.assertEqual(res.output, "1: def greet(name):\n2:     return f'Hello, {name}'")

        bad_range = read_file_lines(self.ws, "src/app.py", start_line=5, end_line=2)
        self.assertFalse(bad_range.ok)
        self.assertIn("Invalid line range", bad_range.error_hint or "")

        binary = read_file_lines(self.ws, "binary.bin")
        self.assertFalse(binary.ok)
        self.assertIn("UTF-8", binary.error_hint or "")

        missing = read_file_lines(self.ws, "does_not_exist.txt")
        self.assertFalse(missing.ok)
        self.assertIn("does not exist", missing.error_hint or "")

        as_dir = read_file_lines(self.ws, "src")
        self.assertFalse(as_dir.ok)
        self.assertIn("directory", as_dir.error_hint or "")

        escape = read_file_lines(self.ws, "../outside/secret.txt")
        self.assertFalse(escape.ok)
        self.assertIn("outside the workspace", escape.error_hint or "")

        (self.ws / "escape_link").symlink_to(self.outside, target_is_directory=True)
        sym_escape = read_file_lines(self.ws, "escape_link/secret.txt")
        self.assertFalse(sym_escape.ok)
        self.assertIn("outside the workspace", sym_escape.error_hint or "")

    def test_grep_search_and_ignored_dirs(self) -> None:
        (self.ws / ".git").mkdir()
        (self.ws / ".git" / "config").write_text("greet", encoding="utf-8")
        (self.ws / ".venv").mkdir()
        (self.ws / ".venv" / "lib.py").write_text("greet", encoding="utf-8")
        (self.ws / "__pycache__").mkdir()
        (self.ws / "__pycache__" / "app.pyc").write_text("greet", encoding="utf-8")
        (self.ws / ".agent-drafts").mkdir()
        (self.ws / ".agent-drafts" / "draft.py").write_text("greet", encoding="utf-8")

        res = grep_search(self.ws, r"def \w+", ".")
        self.assertTrue(res.ok)
        self.assertEqual(len(res.output), 2)
        self.assertEqual(res.output[0]["file"], "src/app.py")
        self.assertEqual(res.output[0]["line"], 1)

        # Ignored dirs must not appear in results; binary.bin is skipped safely.
        greet_res = grep_search(self.ws, "greet", ".")
        self.assertTrue(greet_res.ok)
        self.assertEqual([m["file"] for m in greet_res.output], ["src/app.py"])

        bad_regex = grep_search(self.ws, "[unclosed", ".")
        self.assertFalse(bad_regex.ok)
        self.assertIn("regular expression", bad_regex.error_hint or "")

        binary_single = grep_search(self.ws, "x", "binary.bin")
        self.assertFalse(binary_single.ok)
        self.assertIn("UTF-8", binary_single.error_hint or "")

        escape = grep_search(self.ws, "TOP_SECRET", "../outside")
        self.assertFalse(escape.ok)
        self.assertIn("outside the workspace", escape.error_hint or "")

    def test_apply_search_replace_diff(self) -> None:
        res = apply_search_replace_diff(
            self.ws,
            "notes.md",
            search_block="beta\ngamma",
            replace_block="beta_updated\ngamma",
        )
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.diff)
        self.assertIn("--- a/notes.md", res.diff)
        self.assertIn("+++ b/notes.md", res.diff)
        self.assertIn("-beta", res.diff)
        self.assertIn("+beta_updated", res.diff)
        self.assertEqual(
            (self.ws / "notes.md").read_text(encoding="utf-8"),
            "alpha\nbeta_updated\ngamma",
        )

        not_found = apply_search_replace_diff(
            self.ws, "notes.md", "nonexistent", "replacement"
        )
        self.assertFalse(not_found.ok)
        self.assertIn("not found", not_found.error_hint or "")

        (self.ws / "dup.txt").write_text("item\nitem\n", encoding="utf-8")
        multi = apply_search_replace_diff(self.ws, "dup.txt", "item", "changed")
        self.assertFalse(multi.ok)
        self.assertIn("matched 2 times", multi.error_hint or "")

        empty_search = apply_search_replace_diff(self.ws, "notes.md", "", "x")
        self.assertFalse(empty_search.ok)
        self.assertIn("empty", empty_search.error_hint or "")

        escape = apply_search_replace_diff(
            self.ws, "../outside/secret.txt", "TOP_SECRET", "HACKED"
        )
        self.assertFalse(escape.ok)
        self.assertEqual(
            (self.outside / "secret.txt").read_text(encoding="utf-8"), "TOP_SECRET"
        )

        # CRLF in search_block matches normalized file lines cleanly
        crlf_res = apply_search_replace_diff(
            self.ws, "notes.md", "alpha\r\nbeta_updated", "alpha\r\nbeta_v2"
        )
        self.assertTrue(crlf_res.ok)
        self.assertIn("+beta_v2", crlf_res.diff or "")

    def test_write_note(self) -> None:
        res = write_note(self.ws, "research/summary.md", "# Summary\n- Point 1\n")
        self.assertTrue(res.ok)
        self.assertIn("Point 1", res.diff or "")
        self.assertEqual(
            (self.ws / "research" / "summary.md").read_text(encoding="utf-8"),
            "# Summary\n- Point 1\n",
        )

        as_dir = write_note(self.ws, "research", "oops")
        self.assertFalse(as_dir.ok)
        self.assertIn("directory", as_dir.error_hint or "")

        escape = write_note(self.ws, "../outside/evil.md", "bad")
        self.assertFalse(escape.ok)
        self.assertFalse((self.outside / "evil.md").exists())


class DraftIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "workspace"
        self.outside = self.root / "outside"
        self.ws.mkdir()
        self.outside.mkdir()
        (self.outside / "leak.txt").write_text("EXTERNAL_DATA", encoding="utf-8")
        (self.ws / "report.md").write_text("line1\nline2\n", encoding="utf-8")
        self.manager = DraftWorkspaceManager(self.ws)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_invalid_identifiers_rejected(self) -> None:
        for bad in ("../evil", "a/b", "a\\b", "..", ".hidden", ""):
            with self.assertRaises(ValueError):
                self.manager.draft_dir(bad, "task1")
            with self.assertRaises(ValueError):
                self.manager.draft_dir("coder", bad)

    def test_stage_edit_diff_events_and_promote(self) -> None:
        staged_existing = self.manager.stage_file("coder", "task-1", "report.md")
        staged_new = self.manager.stage_file("coder", "task-1", "docs/new.md")
        self.assertTrue(staged_existing.exists())
        self.assertTrue(staged_new.exists())
        self.assertEqual(
            staged_existing.read_text(encoding="utf-8"), "line1\nline2\n"
        )

        # Unmodified staged file yields no diff yet.
        self.assertEqual(self.manager.compute_diffs("coder", "task-1"), {})

        # Edit inside draft using surgical diff tool; workspace root stays untouched until promote.
        ddir = self.manager.draft_dir("coder", "task-1")
        diff_res = apply_search_replace_diff(
            ddir, "report.md", "line2", "line2_edited"
        )
        self.assertTrue(diff_res.ok)
        staged_new.write_text("# New Doc\n", encoding="utf-8")
        self.assertEqual(
            (self.ws / "report.md").read_text(encoding="utf-8"), "line1\nline2\n"
        )
        self.assertFalse((self.ws / "docs" / "new.md").exists())

        diffs = self.manager.compute_diffs("coder", "task-1")
        self.assertEqual(set(diffs.keys()), {"docs/new.md", "report.md"})
        self.assertIn("-line2", diffs["report.md"])
        self.assertIn("+line2_edited", diffs["report.md"])
        self.assertIn("+# New Doc", diffs["docs/new.md"])

        events = self.manager.build_diff_events("coder", "task-1")
        self.assertEqual(len(events), 2)
        for ev in events:
            self.assertIsInstance(ev, AgentEventPayload)
            self.assertEqual(ev.event_type, EventType.DIFF_PROPOSED)
            self.assertEqual(ev.agent_id, "coder")
            self.assertEqual(ev.task_id, "task-1")
            self.assertIn(ev.payload["path"], diffs)
            self.assertEqual(ev.payload["diff"], diffs[ev.payload["path"]])

        promoted = self.manager.promote_draft("coder", "task-1")
        self.assertEqual(promoted, ["docs/new.md", "report.md"])
        self.assertEqual(
            (self.ws / "report.md").read_text(encoding="utf-8"),
            "line1\nline2_edited\n",
        )
        self.assertEqual(
            (self.ws / "docs" / "new.md").read_text(encoding="utf-8"), "# New Doc\n"
        )
        self.assertFalse(self.manager.draft_dir("coder", "task-1").exists())

    def test_discard_draft_leaves_workspace_untouched(self) -> None:
        staged = self.manager.stage_file("writer", "task-2", "report.md")
        staged.write_text("discarded content\n", encoding="utf-8")
        draft_path = self.manager.draft_dir("writer", "task-2")
        self.assertTrue(draft_path.exists())

        self.manager.discard_draft("writer", "task-2")
        self.assertFalse(draft_path.exists())
        self.assertEqual(
            (self.ws / "report.md").read_text(encoding="utf-8"), "line1\nline2\n"
        )
        # Idempotent when draft does not exist.
        self.manager.discard_draft("writer", "task-2")

    def test_symlink_escape_blocked(self) -> None:
        # 1. Symlink inside workspace escaping outside during stage_file
        (self.ws / "out_link").symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(PathEscapeError):
            self.manager.stage_file("coder", "t1", "out_link/leak.txt")

        # 2. Symlink planted inside a draft directory blocked before compute_diffs / promote
        ddir = self.manager.draft_dir("coder", "t2")
        ddir.mkdir(parents=True, exist_ok=True)
        (ddir / "planted.txt").symlink_to(self.outside / "leak.txt")
        with self.assertRaises(PathEscapeError):
            self.manager.compute_diffs("coder", "t2")
        with self.assertRaises(PathEscapeError):
            self.manager.promote_draft("coder", "t2")
        self.assertFalse((self.ws / "planted.txt").exists())

        # 3. Replacing .agent-drafts with a symlink to outside directory
        ws2 = self.root / "ws2"
        ws2.mkdir()
        (ws2 / ".agent-drafts").symlink_to(self.outside, target_is_directory=True)
        mgr2 = DraftWorkspaceManager(ws2)
        with self.assertRaises(PathEscapeError):
            mgr2.draft_dir("coder", "t1")
        with self.assertRaises(PathEscapeError):
            mgr2.discard_draft("coder", "t1")
        self.assertTrue((self.outside / "leak.txt").exists())

        # 4. Staging .agent-drafts path, workspace root, or directory fails cleanly
        with self.assertRaises(PathEscapeError):
            self.manager.stage_file("coder", "t3", ".agent-drafts/x.txt")
        with self.assertRaises(PathEscapeError):
            self.manager.stage_file("coder", "t3", ".")
        (self.ws / "subdir").mkdir()
        with self.assertRaises(IsADirectoryError):
            self.manager.stage_file("coder", "t3", "subdir")


class DockerSandboxAndWatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ws = self.root / "workspace"
        self.ws.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_build_container_kwargs_maps_resource_profile(self) -> None:
        prev_cwd = Path.cwd()
        try:
            os.chdir(self.root)
            default_kwargs = DockerSandboxManager.build_container_kwargs(
                "workspace", ["pytest", "-q"]
            )
        finally:
            os.chdir(prev_cwd)

        self.assertEqual(default_kwargs["image"], "python:3.11-slim")
        self.assertEqual(default_kwargs["command"], ["pytest", "-q"])
        self.assertEqual(default_kwargs["mem_limit"], "4096m")
        self.assertEqual(default_kwargs["nano_cpus"], 2_000_000_000)
        self.assertTrue(default_kwargs["network_disabled"])
        self.assertEqual(
            default_kwargs["volumes"],
            {str(self.ws.resolve()): {"bind": "/workspace", "mode": "rw"}},
        )
        self.assertEqual(default_kwargs["working_dir"], "/workspace")
        self.assertNotIn("device_requests", default_kwargs)

        custom_profile = ResourceProfile(
            mem_limit_mb=1024,
            cpu_cores=1.5,
            gpu_ids=["0"],
            network_enabled=True,
        )
        custom_kwargs = DockerSandboxManager.build_container_kwargs(
            self.ws, "python script.py", profile=custom_profile, image="python:3.12-slim"
        )
        self.assertEqual(custom_kwargs["image"], "python:3.12-slim")
        self.assertEqual(custom_kwargs["mem_limit"], "1024m")
        self.assertEqual(custom_kwargs["nano_cpus"], 1_500_000_000)
        self.assertFalse(custom_kwargs["network_disabled"])
        self.assertEqual(len(custom_kwargs["device_requests"]), 1)

        with self.assertRaises(ValueError):
            DockerSandboxManager.build_container_kwargs(self.root / "missing", "echo hi")

    def test_run_command_with_mock_client_and_daemon_unavailable(self) -> None:
        mock_client = MagicMock()
        mock_client.containers.run.return_value = b"2 passed\n"
        mgr = DockerSandboxManager(docker_client=mock_client)

        res = mgr.run_command(self.ws, ["pytest", "-q"])
        self.assertIsInstance(res, SandboxRunResult)
        self.assertTrue(res.ok)
        self.assertEqual(res.exit_code, 0)
        self.assertEqual(res.output, "2 passed\n")
        mock_client.containers.run.assert_called_once()
        _, call_kwargs = mock_client.containers.run.call_args
        self.assertEqual(call_kwargs["mem_limit"], "4096m")
        self.assertTrue(call_kwargs["network_disabled"])

        # Container non-zero exit
        failing_client = MagicMock()
        failing_client.containers.run.side_effect = ContainerError(
            container=MagicMock(),
            exit_status=2,
            command="pytest",
            image="python:3.11-slim",
            stderr=b"AssertionError",
        )
        fail_res = mgr.run_command(self.ws, "pytest", docker_client=failing_client)
        self.assertFalse(fail_res.ok)
        self.assertEqual(fail_res.exit_code, 2)
        self.assertIn("AssertionError", fail_res.output)
        self.assertIn("exit code 2", fail_res.error_hint or "")

        # Docker daemon not running: safe non-crashing result
        no_daemon_mgr = DockerSandboxManager()
        with patch(
            "backend.app.sandbox.manager.docker.from_env",
            side_effect=DockerException("daemon not running"),
        ):
            offline_res = no_daemon_mgr.run_command(self.ws, "echo hello")
        self.assertFalse(offline_res.ok)
        self.assertEqual(offline_res.exit_code, -1)
        self.assertIn("Docker daemon unavailable", offline_res.error_hint or "")

    def test_workspace_file_event_handler_filters_noisy_dirs(self) -> None:
        received: list[AgentEventPayload] = []
        handler = WorkspaceFileEventHandler(
            self.ws,
            callback=received.append,
            agent_id="watcher",
            task_id="t99",
        )

        # Ignored paths
        handler.dispatch(FileModifiedEvent(str(self.ws / ".git" / "HEAD")))
        handler.dispatch(FileCreatedEvent(str(self.ws / ".venv" / "Lib" / "a.py")))
        handler.dispatch(FileModifiedEvent(str(self.ws / "backend" / "__pycache__" / "x.pyc")))
        handler.dispatch(FileCreatedEvent(str(self.ws / ".agent-drafts" / "coder-t1" / "f.py")))
        handler.dispatch(DirCreatedEvent(str(self.ws / "docs")))
        handler.dispatch(DirModifiedEvent(str(self.ws)))
        handler.dispatch(FileModifiedEvent(str(self.root / "outside.txt")))
        self.assertEqual(received, [])
        self.assertEqual(handler.events, [])

        # Valid workspace file events (including .gitignore which is not inside .git/)
        handler.dispatch(FileCreatedEvent(str(self.ws / "notes.md")))
        handler.dispatch(FileModifiedEvent(str(self.ws / ".gitignore")))
        handler.dispatch(FileDeletedEvent(str(self.ws / "old.txt")))
        handler.dispatch(
            FileMovedEvent(
                str(self.ws / ".agent-drafts" / "coder-t1" / "final.md"),
                str(self.ws / "docs" / "final.md"),
            )
        )

        self.assertEqual(len(received), 4)
        self.assertEqual(len(handler.events), 4)
        for ev in received:
            self.assertEqual(ev.event_type, EventType.ARTIFACT_UPDATED)
            self.assertEqual(ev.agent_id, "watcher")
            self.assertEqual(ev.task_id, "t99")

        self.assertEqual(
            [(ev.payload["path"], ev.payload["event_type"]) for ev in received],
            [
                ("notes.md", "created"),
                (".gitignore", "modified"),
                ("old.txt", "deleted"),
                ("docs/final.md", "moved"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
