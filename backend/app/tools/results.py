"""Structured tool results that the LLM can read and self-correct from (CLAUDE.md §4.5)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from backend.app.core.events import AgentEventPayload, EventType


class ToolResult(BaseModel):
    """Outcome of one tool call. Tools return this instead of raising."""

    tool: str
    ok: bool
    content: str = ""
    error_code: str | None = None
    error: str | None = None
    hint: str | None = None
    path: str | None = None
    diff: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def success(cls, tool: str, content: str, **extra: Any) -> "ToolResult":
        return cls(tool=tool, ok=True, content=content, **extra)

    @classmethod
    def failure(
        cls, tool: str, code: str, error: str, hint: str, path: str | None = None
    ) -> "ToolResult":
        return cls(tool=tool, ok=False, error_code=code, error=error, hint=hint, path=path)

    @property
    def error_signature(self) -> str | None:
        """Stable key for SubAgentState.record_error (same error 3x -> NEEDS_HUMAN_HELP)."""
        if self.ok:
            return None
        return f"{self.tool}:{self.error_code}:{self.path or ''}"

    def to_message_text(self) -> str:
        """Text placed in the ToolMessage the LLM sees."""
        if self.ok:
            return self.content
        return f"ERROR [{self.error_code}]: {self.error}\nHOW TO FIX: {self.hint}"


def tool_events(result: ToolResult, agent_id: str, task_id: str) -> list[AgentEventPayload]:
    """WebSocket events for a finished tool call: always TOOL_CALL_RESULT, plus DIFF_PROPOSED for edits."""
    events = [
        AgentEventPayload(
            event_type=EventType.TOOL_CALL_RESULT,
            agent_id=agent_id,
            task_id=task_id,
            payload={
                "tool": result.tool,
                "ok": result.ok,
                "error_code": result.error_code,
                "path": result.path,
                "summary": (result.content if result.ok else result.error or "")[:500],
            },
        )
    ]
    if result.ok and result.diff:
        events.append(
            AgentEventPayload(
                event_type=EventType.DIFF_PROPOSED,
                agent_id=agent_id,
                task_id=task_id,
                payload={"path": result.path, "diff": result.diff, **result.data},
            )
        )
    return events
