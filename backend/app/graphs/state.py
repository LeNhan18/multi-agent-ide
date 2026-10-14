"""Agent state definitions (CLAUDE.md §4.2, §4.3)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    NEEDS_HUMAN_HELP = "NEEDS_HUMAN_HELP"
    DONE = "DONE"
    FAILED = "FAILED"


class ResourceProfile(BaseModel):
    mem_limit_mb: int = 4096
    cpu_cores: float = 2.0
    gpu_ids: list[str] = Field(default_factory=list)
    max_vram_mb: int | None = None
    network_enabled: bool = False


class TaskItem(BaseModel):
    task_id: str
    title: str
    assigned_agent: str | None = None
    status: TaskStatus = TaskStatus.PENDING
    worktree_path: str | None = None
    branch: str | None = None


class SharedWorkspaceState(BaseModel):
    """Compact state visible to every agent."""

    workspace_root: str
    tasks: list[TaskItem] = Field(default_factory=list)
    directory_summary: str = ""
    architecture_decisions: list[str] = Field(default_factory=list)
    step_count: int = 0
    accumulated_cost_usd: float = 0.0
    budget_limit_usd: float = 5.0
    max_steps: int = 100


class SubAgentState(BaseModel):
    """Private, per-agent working memory; never shared with other agents."""

    agent_id: str
    task_id: str
    goal: str
    messages: list[dict] = Field(default_factory=list)
    modified_files: list[str] = Field(default_factory=list)
    current_error: str | None = None
    error_signature_attempts: dict[str, int] = Field(default_factory=dict)
    token_count: int = 0
    resources: ResourceProfile = Field(default_factory=ResourceProfile)

    MAX_SAME_ERROR_ATTEMPTS: int = 3

    def record_error(self, signature: str) -> bool:
        """Record a failure; return True when human help is required."""
        self.current_error = signature
        count = self.error_signature_attempts.get(signature, 0) + 1
        self.error_signature_attempts[signature] = count
        return count >= self.MAX_SAME_ERROR_ATTEMPTS
