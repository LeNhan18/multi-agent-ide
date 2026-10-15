"""Unified WebSocket event schema (CLAUDE.md §4.4)."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class EventType(str, Enum):
    AGENT_STATE_CHANGE = "AGENT_STATE_CHANGE"
    TOOL_CALL_START = "TOOL_CALL_START"
    TOOL_CALL_RESULT = "TOOL_CALL_RESULT"
    TERMINAL_OUTPUT = "TERMINAL_OUTPUT"
    ARTIFACT_UPDATED = "ARTIFACT_UPDATED"
    DIFF_PROPOSED = "DIFF_PROPOSED"
    HITL_INTERRUPT_REQUIRED = "HITL_INTERRUPT_REQUIRED"
    TOKEN_USAGE_UPDATE = "TOKEN_USAGE_UPDATE"
    RESOURCE_USAGE_UPDATE = "RESOURCE_USAGE_UPDATE"


class AgentEventPayload(BaseModel):
    event_type: EventType
    agent_id: str
    task_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    payload: dict[str, Any] = Field(default_factory=dict)
