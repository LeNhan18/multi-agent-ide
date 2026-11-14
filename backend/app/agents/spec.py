"""Dynamic, domain-agnostic agent configuration (CLAUDE.md §4.1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.agents.catalog import (
    ALWAYS_HITL,
    MODELS,
    REVIEWER_PERMISSIONS,
    SUPERVISOR_PERMISSIONS,
    TOOL_PERMISSIONS,
    Permission,
    Provider,
)

Domain = Literal["research", "data_finance", "content", "legal", "engineering", "custom"]
DEFAULT_MODEL: dict[str, str] = {
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.4-mini",
    "gemini": "gemini-3.8-flash",
}


class ModelSettings(BaseModel):
    """Model choice for one agent. ``provider`` is derived from ``model`` via the catalog."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider = "anthropic"
    model: str = DEFAULT_MODEL["anthropic"]
    effort: Literal["low", "medium", "high"] | None = None
    max_output_tokens: int = Field(default=4096, ge=256, le=64_000)
    context_window: int = Field(default=200_000, ge=8_000)

    @model_validator(mode="before")
    @classmethod
    def _fill_from_catalog(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "model" not in data:
            data["model"] = DEFAULT_MODEL.get(data.get("provider", "anthropic"), DEFAULT_MODEL["anthropic"])
        entry = MODELS.get(data["model"])
        if entry is None:
            raise ValueError(f"unknown model {data['model']!r}; choose one of {sorted(MODELS)}")
        if "provider" in data and data["provider"] != entry[0]:
            raise ValueError(f"model {data['model']} belongs to provider {entry[0]}, not {data['provider']}")
        data["provider"] = entry[0]
        return data


class AgentSpec(BaseModel):
    """Everything needed to instantiate one agent node at runtime."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    role_name: str
    system_prompt: str
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    llm: ModelSettings = Field(default_factory=ModelSettings, alias="model_config")
    is_reviewer: bool = False
    reports_to: str | None = None
    reviews: list[str] = Field(default_factory=list)
    permissions: list[Permission] = Field(default_factory=list)
    budget_usd: float = Field(default=1.0, ge=0, allow_inf_nan=False)
    hitl_tools: list[str] = Field(
        default_factory=list,
        description="Tools that require human approval (interrupt_before).",
    )

    @model_validator(mode="after")
    def _validate_tools(self) -> "AgentSpec":
        unknown = [t for t in self.allowed_mcp_tools if t not in TOOL_PERMISSIONS]
        if unknown:
            raise ValueError(f"{self.role_name}: unknown tools {unknown} are denied")
        missing = sorted({TOOL_PERMISSIONS[t].value for t in self.allowed_mcp_tools} - {p.value for p in self.permissions})
        if missing:
            raise ValueError(f"{self.role_name}: allowed tools need permissions {missing}")
        stray = [t for t in self.hitl_tools if t not in self.allowed_mcp_tools]
        if stray:
            raise ValueError(f"{self.role_name}: hitl_tools {stray} are not in allowed_mcp_tools")
        unguarded = sorted(ALWAYS_HITL.intersection(self.allowed_mcp_tools) - set(self.hitl_tools))
        if unguarded:
            raise ValueError(f"{self.role_name}: {unguarded} must be listed in hitl_tools")
        if self.is_reviewer and not set(self.permissions) <= REVIEWER_PERMISSIONS:
            raise ValueError(f"{self.role_name}: reviewers may only read or run checks, not edit")
        return self


class SquadTemplate(BaseModel):
    """A team of agents for one domain, loaded from templates/*.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    domain: Domain
    description: str = ""
    supervisor: AgentSpec
    members: list[AgentSpec]
    max_steps: int = 100
    budget_limit_usd: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    max_review_rejections: int = 3

    @model_validator(mode="after")
    def _validate_roles(self) -> "SquadTemplate":
        names = [m.role_name for m in self.members]
        if len(names) != len(set(names)):
            raise ValueError("member role_name values must be unique")
        if self.supervisor.role_name in names:
            raise ValueError("supervisor role_name must differ from members")
        if self.supervisor.is_reviewer or not set(self.supervisor.permissions) <= SUPERVISOR_PERMISSIONS:
            raise ValueError("supervisor routes work only: it cannot review, edit, execute or reach outside")
        total = self.supervisor.budget_usd + sum(m.budget_usd for m in self.members)
        if total > self.budget_limit_usd + 1e-9:
            raise ValueError(f"agent budgets total {total:.2f} USD, above budget_limit_usd {self.budget_limit_usd:.2f}")
        agents = [self.supervisor, *self.members]
        by_name = {agent.role_name: agent for agent in agents}
        for agent in agents:
            if agent.reports_to is not None and (agent.reports_to not in by_name or agent.reports_to == agent.role_name):
                raise ValueError(f"invalid reports_to for {agent.role_name}")
            if agent.reviews and not agent.is_reviewer:
                raise ValueError(f"{agent.role_name} is not a reviewer")
            for target in agent.reviews:
                if target not in by_name or target == agent.role_name:
                    raise ValueError(f"invalid review target {target}")
                if agent.llm.provider == by_name[target].llm.provider:
                    raise ValueError(f"reviewer and {target} must use different providers")
        for member in self.members:
            if not member.is_reviewer and not any(member.role_name in reviewer.reviews for reviewer in agents if reviewer.is_reviewer):
                raise ValueError(f"{member.role_name} has no reviewer")
        return self


def load_squad(path: str | Path) -> SquadTemplate:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return SquadTemplate.model_validate(data)


def load_all_squads(directory: str | Path) -> dict[str, SquadTemplate]:
    squads: dict[str, SquadTemplate] = {}
    for file in sorted(Path(directory).glob("*.y*ml")):
        squad = load_squad(file)
        squads[squad.name] = squad
    return squads
