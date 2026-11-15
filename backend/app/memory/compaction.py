"""Context compaction and shared blackboard memory (CLAUDE.md §4.4)."""
from __future__ import annotations

import json
import re
from typing import Any

from backend.app.graphs.state import SharedWorkspaceState, SubAgentState, TaskItem, TaskStatus

_DOC_PATH_RE = re.compile(
    r"(?:https?://[^\s)\]>'\"`]+|"
    r"(?:[A-Za-z]:)?[A-Za-z0-9_./\\-]+\."
    r"(?:pdf|docx?|xlsx?|csv|md|txt|ya?ml|json|py|ts|tsx|js|mjs|sql|html)\b)",
    re.IGNORECASE,
)

_FIGURE_RE = re.compile(
    r"(?:(?:\$|€|£|¥|USD|EUR|VND)\s?\d[\d,]*(?:\.\d+)?(?:\s?[KMBTkmbt])?|"
    r"\b\d[\d,]*(?:\.\d+)?\s?(?:%|percent|USD|EUR|VND|MB|GB|KB|ms|rows|pages|items|tokens|clauses)\b|"
    r"\b[A-Za-z_][A-Za-z0-9_ -]{1,24}[:=]\s*[$€£]?\d[\d,]*(?:\.\d+)?%?[KMBT]?)",
    re.IGNORECASE,
)


def _message_parts(message: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for value in message.values():
        if value is None:
            continue
        if isinstance(value, str):
            parts.append(value)
        else:
            parts.append(json.dumps(value, ensure_ascii=False, default=str))
    return parts


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if content is not None:
        return json.dumps(content, ensure_ascii=False, default=str)
    return " ".join(_message_parts(message))


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Estimate token count for a list of chat/tool message dictionaries."""
    if not messages:
        return 0
    total = 0
    for message in messages:
        char_count = sum(len(part) for part in _message_parts(message))
        # ~4 chars/token heuristic plus 4 tokens per message framing overhead.
        total += 4 + ((char_count + 3) // 4)
    return total


def should_compact(
    sub_state: SubAgentState,
    context_window: int,
    threshold: float = 0.75,
) -> bool:
    """Return True when sub-agent token usage meets or exceeds the compaction threshold."""
    if not (0.0 < threshold <= 1.0):
        raise ValueError("threshold must be within (0.0, 1.0]")
    if context_window <= 0:
        return False
    effective_tokens = max(sub_state.token_count, estimate_tokens(sub_state.messages))
    return effective_tokens >= (context_window * threshold)


def _dedupe_preserve_order(items: list[str], limit: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        cleaned = item.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
        if len(result) >= limit:
            break
    return result


def _extract_compaction_summary(
    sub_state: SubAgentState,
    older_messages: list[dict[str, Any]],
) -> str:
    docs: list[str] = list(sub_state.modified_files)
    figures: list[str] = []
    highlights: list[str] = []

    for msg in older_messages:
        text = _message_text(msg).strip()
        if not text:
            continue
        docs.extend(_DOC_PATH_RE.findall(text))
        figures.extend(_FIGURE_RE.findall(text))

        # Keep concise single-line highlights while skipping prior summary headers.
        first_line = next(
            (
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.strip().startswith("[Context Summary")
            ),
            "",
        )
        if first_line:
            snippet = first_line[:140] + ("..." if len(first_line) > 140 else "")
            role = str(msg.get("role", "message")).strip() or "message"
            highlights.append(f"[{role}] {snippet}")

    unique_docs = _dedupe_preserve_order(docs, limit=15)
    unique_figures = _dedupe_preserve_order(figures, limit=15)
    unique_highlights = _dedupe_preserve_order(highlights, limit=6)

    lines = [
        f"[Context Summary - {len(older_messages)} earlier messages compacted]",
        f"Original Goal: {sub_state.goal}",
        f"Modified Files: {', '.join(sub_state.modified_files) if sub_state.modified_files else 'None'}",
        f"Current Error: {sub_state.current_error if sub_state.current_error else 'None'}",
        f"Extracted Documents/Refs: {', '.join(unique_docs) if unique_docs else 'None'}",
        f"Extracted Figures/Metrics: {', '.join(unique_figures) if unique_figures else 'None'}",
    ]
    if unique_highlights:
        lines.append("Key Prior Turns:")
        lines.extend(f"- {item}" for item in unique_highlights)

    return "\n".join(lines)


def compact_subagent_context(
    sub_state: SubAgentState,
    context_window: int,
    threshold: float = 0.75,
    keep_recent: int = 5,
) -> SubAgentState:
    """Compact older SubAgentState messages into one system summary when >= threshold."""
    if keep_recent < 0:
        raise ValueError("keep_recent must be non-negative")

    if not should_compact(sub_state, context_window, threshold=threshold) or len(sub_state.messages) <= keep_recent:
        return sub_state.model_copy(deep=True)

    if keep_recent == 0:
        older_messages = list(sub_state.messages)
        recent_messages: list[dict[str, Any]] = []
    else:
        older_messages = list(sub_state.messages[:-keep_recent])
        recent_messages = list(sub_state.messages[-keep_recent:])

    summary_text = _extract_compaction_summary(sub_state, older_messages)
    summary_message: dict[str, Any] = {"role": "system", "content": summary_text}
    new_messages = [summary_message, *recent_messages]
    new_token_count = estimate_tokens(new_messages)

    return sub_state.model_copy(
        update={
            "messages": new_messages,
            "token_count": new_token_count,
        },
        deep=True,
    )


class SharedBlackboard:
    """Manages SharedWorkspaceState while isolating raw SubAgentState histories."""

    def __init__(
        self,
        state: SharedWorkspaceState | None = None,
        *,
        workspace_root: str = ".",
        budget_limit_usd: float = 5.0,
        max_steps: int = 100,
    ) -> None:
        self._state = (
            state
            if state is not None
            else SharedWorkspaceState(
                workspace_root=workspace_root,
                budget_limit_usd=budget_limit_usd,
                max_steps=max_steps,
            )
        )

    @property
    def state(self) -> SharedWorkspaceState:
        return self._state

    def add_architecture_decision(self, decision: str) -> SharedWorkspaceState:
        cleaned = decision.strip()
        if not cleaned:
            raise ValueError("architecture decision must not be empty")
        if cleaned not in self._state.architecture_decisions:
            self._state.architecture_decisions.append(cleaned)
        return self._state

    def update_directory_summary(self, summary: str) -> SharedWorkspaceState:
        self._state.directory_summary = summary.strip()
        return self._state

    def add_task(
        self,
        task_id: str,
        title: str,
        *,
        assigned_agent: str | None = None,
        status: TaskStatus | str = TaskStatus.PENDING,
        worktree_path: str | None = None,
        branch: str | None = None,
    ) -> TaskItem:
        if any(task.task_id == task_id for task in self._state.tasks):
            raise ValueError(f"Task '{task_id}' already exists")
        item = TaskItem(
            task_id=task_id,
            title=title,
            assigned_agent=assigned_agent,
            status=TaskStatus(status),
            worktree_path=worktree_path,
            branch=branch,
        )
        self._state.tasks.append(item)
        return item

    def update_task_status(
        self,
        task_id: str,
        status: TaskStatus | str,
        *,
        assigned_agent: str | None = None,
    ) -> TaskItem:
        normalized = TaskStatus(status)
        for task in self._state.tasks:
            if task.task_id == task_id:
                task.status = normalized
                if assigned_agent is not None:
                    task.assigned_agent = assigned_agent
                return task
        raise KeyError(f"Task '{task_id}' not found on shared blackboard")

    def sync_subagent_outcome(
        self,
        sub_state: SubAgentState,
        *,
        task_status: TaskStatus | str | None = None,
        verified_decision: str | None = None,
    ) -> SharedWorkspaceState:
        """Promote verified outcomes/status from SubAgentState without copying raw messages."""
        if task_status is not None:
            existing = next((t for t in self._state.tasks if t.task_id == sub_state.task_id), None)
            if existing is not None:
                existing.status = TaskStatus(task_status)
                if not existing.assigned_agent:
                    existing.assigned_agent = sub_state.agent_id
            else:
                self._state.tasks.append(
                    TaskItem(
                        task_id=sub_state.task_id,
                        title=sub_state.goal,
                        assigned_agent=sub_state.agent_id,
                        status=TaskStatus(task_status),
                    )
                )
        if verified_decision:
            self.add_architecture_decision(verified_decision)
        return self._state

    def to_dict(self) -> dict[str, Any]:
        return self._state.model_dump(mode="json")
