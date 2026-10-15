"""Dynamic, domain-agnostic agent configuration (CLAUDE.md §4.1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

Domain = Literal["research", "data_finance", "content", "legal", "engineering", "custom"]


class ModelSettings(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-5-5"
    temperature: float = 0.2
    max_output_tokens: int = 4096
    context_window: int = 200_000


class AgentSpec(BaseModel):
    """Everything needed to instantiate one agent node at runtime."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    role_name: str
    system_prompt: str
    allowed_mcp_tools: list[str] = Field(default_factory=list)
    output_schema: dict[str, Any] | None = None
    llm: ModelSettings = Field(default_factory=ModelSettings, alias="model_config")
    is_reviewer: bool = False
    hitl_tools: list[str] = Field(
        default_factory=list,
        description="Tools that require human approval (interrupt_before).",
    )


class SquadTemplate(BaseModel):
    """A team of agents for one domain, loaded from templates/*.yaml."""

    model_config = ConfigDict(extra="forbid")

    name: str
    domain: Domain
    description: str = ""
    supervisor: AgentSpec
    members: list[AgentSpec]
    max_steps: int = 100
    budget_limit_usd: float = 5.0
    max_review_rejections: int = 3

    @model_validator(mode="after")
    def _validate_roles(self) -> "SquadTemplate":
        names = [m.role_name for m in self.members]
        if len(names) != len(set(names)):
            raise ValueError("member role_name values must be unique")
        if self.supervisor.role_name in names:
            raise ValueError("supervisor role_name must differ from members")
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
