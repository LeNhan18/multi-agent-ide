"""Docker container sandbox runner and Watchdog workspace event handler (CLAUDE.md §2, §4.5)."""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import docker
from docker.errors import APIError, ContainerError, DockerException
from docker.types import DeviceRequest
from pydantic import BaseModel, Field
from watchdog.events import FileSystemEvent, FileSystemEventHandler

from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs.state import ResourceProfile

IGNORED_WATCH_DIRS: frozenset[str] = frozenset(
    {".git", ".venv", "__pycache__", ".agent-drafts"}
)


class SandboxRunResult(BaseModel):
    """Structured outcome of running a command inside a Docker sandbox."""

    ok: bool
    exit_code: int = 0
    output: str = ""
    error_hint: str | None = None
    container_kwargs: dict[str, Any] = Field(default_factory=dict)


def _decode_output(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        return raw
    if isinstance(raw, Iterable):
        chunks = [
            chunk.decode("utf-8", errors="replace") if isinstance(chunk, bytes) else str(chunk)
            for chunk in raw
        ]
        return "".join(chunks)
    return str(raw)


class DockerSandboxManager:
    """Build resource-constrained Docker container specs and run sandboxed commands."""

    def __init__(self, docker_client: Any | None = None) -> None:
        self.docker_client = docker_client

    @staticmethod
    def build_container_kwargs(
        workspace_root: str | Path,
        command: list[str] | str,
        profile: ResourceProfile | None = None,
        image: str = "python:3.11-slim",
    ) -> dict[str, Any]:
        """Translate workspace_root and ResourceProfile into docker SDK containers.run kwargs."""
        resolved_root = Path(workspace_root).resolve()
        if not resolved_root.exists() or not resolved_root.is_dir():
            raise ValueError(
                f"workspace_root must be an existing directory: '{resolved_root}'"
            )

        prof = profile or ResourceProfile()
        kwargs: dict[str, Any] = {
            "image": image,
            "command": command,
            "volumes": {str(resolved_root): {"bind": "/workspace", "mode": "rw"}},
            "working_dir": "/workspace",
            "mem_limit": f"{prof.mem_limit_mb}m",
            "nano_cpus": int(prof.cpu_cores * 1_000_000_000),
            "network_disabled": not prof.network_enabled,
            "remove": True,
            "stdout": True,
            "stderr": True,
        }
        if prof.gpu_ids:
            kwargs["device_requests"] = [
                DeviceRequest(device_ids=list(prof.gpu_ids), capabilities=[["gpu"]])
            ]
        return kwargs

    def run_command(
        self,
        workspace_root: str | Path,
        command: list[str] | str,
        profile: ResourceProfile | None = None,
        image: str = "python:3.11-slim",
        docker_client: Any | None = None,
    ) -> SandboxRunResult:
        """Execute a command in an isolated container, handling daemon unavailability safely."""
        try:
            kwargs = self.build_container_kwargs(
                workspace_root, command, profile=profile, image=image
            )
        except (OSError, ValueError) as exc:
            return SandboxRunResult(ok=False, exit_code=-1, error_hint=str(exc))

        client = docker_client if docker_client is not None else self.docker_client
        if client is None:
            try:
                client = docker.from_env()
            except (DockerException, OSError) as exc:
                return SandboxRunResult(
                    ok=False,
                    exit_code=-1,
                    error_hint=(
                        f"Docker daemon unavailable: {exc}. "
                        "Start Docker or inject a mock docker_client for testing."
                    ),
                    container_kwargs=kwargs,
                )

        try:
            raw = client.containers.run(**kwargs)
        except ContainerError as exc:
            stderr_text = _decode_output(getattr(exc, "stderr", b""))
            exit_code = int(getattr(exc, "exit_status", 1))
            return SandboxRunResult(
                ok=False,
                exit_code=exit_code,
                output=stderr_text,
                error_hint=f"Sandbox command failed with exit code {exit_code}: {stderr_text or exc}",
                container_kwargs=kwargs,
            )
        except (DockerException, APIError, OSError) as exc:
            return SandboxRunResult(
                ok=False,
                exit_code=-1,
                error_hint=f"Docker execution error: {exc}",
                container_kwargs=kwargs,
            )

        return SandboxRunResult(
            ok=True,
            exit_code=0,
            output=_decode_output(raw),
            container_kwargs=kwargs,
        )


class WorkspaceFileEventHandler(FileSystemEventHandler):
    """Watchdog handler filtering noisy directories and emitting ARTIFACT_UPDATED events."""

    def __init__(
        self,
        workspace_root: str | Path,
        callback: Callable[[AgentEventPayload], Any] | None = None,
        agent_id: str = "workspace-watcher",
        task_id: str = "watch",
        ignored_dirs: Iterable[str] | None = None,
    ) -> None:
        super().__init__()
        self.workspace_root = Path(workspace_root).resolve()
        self.callback = callback
        self.agent_id = agent_id
        self.task_id = task_id
        self.ignored_dirs: frozenset[str] = (
            frozenset(ignored_dirs) if ignored_dirs is not None else IGNORED_WATCH_DIRS
        )
        self.events: list[AgentEventPayload] = []

    def _relative_if_allowed(self, raw_path: bytes | str) -> Path | None:
        if not raw_path:
            return None
        path_obj = Path(os.fsdecode(raw_path))
        candidate = path_obj if path_obj.is_absolute() else self.workspace_root / path_obj
        try:
            resolved = candidate.resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(self.workspace_root):
            return None
        rel = resolved.relative_to(self.workspace_root)
        if not rel.parts or any(part in self.ignored_dirs for part in rel.parts):
            return None
        return rel

    def _record_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if event.event_type == "moved":
            dest_rel = self._relative_if_allowed(event.dest_path)
            if dest_rel is None:
                return
            src_rel = self._relative_if_allowed(event.src_path)
            payload_data: dict[str, Any] = {
                "path": dest_rel.as_posix(),
                "event_type": "moved",
            }
            if src_rel is not None:
                payload_data["src_path"] = src_rel.as_posix()
        else:
            rel = self._relative_if_allowed(event.src_path)
            if rel is None:
                return
            payload_data = {
                "path": rel.as_posix(),
                "event_type": event.event_type,
            }

        payload = AgentEventPayload(
            event_type=EventType.ARTIFACT_UPDATED,
            agent_id=self.agent_id,
            task_id=self.task_id,
            payload=payload_data,
        )
        self.events.append(payload)
        if self.callback is not None:
            self.callback(payload)

    def on_created(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._record_event(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        self._record_event(event)
