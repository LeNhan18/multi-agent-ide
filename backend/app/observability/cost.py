"""Token cost and budget circuit breaker (CLAUDE.md §4.3).

Prices are configuration, not code: they change per provider and per account. Fill
``config/model_prices.yaml`` (or point ``MODEL_PRICES_PATH`` elsewhere) from each provider's
pricing page. A model without a price cannot be called through the metered client, so spend is
never untracked.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_PRICES_PATH = Path(__file__).resolve().parents[3] / "config" / "model_prices.yaml"


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class ModelPrice(BaseModel):
    input_per_mtok: float = Field(ge=0, allow_inf_nan=False)
    output_per_mtok: float = Field(ge=0, allow_inf_nan=False)


class PriceNotConfigured(LookupError):
    def __init__(self, model: str) -> None:
        super().__init__(f"No price configured for {model}; add it to config/model_prices.yaml")
        self.model = model


class BudgetExceeded(RuntimeError):
    def __init__(self, scope: str, limit: float, spent: float, needed: float) -> None:
        super().__init__(f"{scope} budget {limit:.4f} USD would be exceeded (spent {spent:.4f}, next call up to {needed:.4f})")
        self.scope, self.limit, self.spent, self.needed = scope, limit, spent, needed


class PriceTable:
    def __init__(self, prices: dict[str, ModelPrice]) -> None:
        self._prices = prices

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PriceTable":
        file = Path(path or os.environ.get("MODEL_PRICES_PATH") or DEFAULT_PRICES_PATH)
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) if file.exists() else {}
        prices = {
            model: ModelPrice.model_validate(value)
            for model, value in (raw or {}).items()
            if isinstance(value, dict) and value.get("input_per_mtok") is not None and value.get("output_per_mtok") is not None
        }
        return cls(prices)

    def price(self, model: str) -> ModelPrice:
        if model not in self._prices:
            raise PriceNotConfigured(model)
        return self._prices[model]

    def cost(self, model: str, usage: Usage) -> float:
        p = self.price(model)
        return (usage.input_tokens * p.input_per_mtok + usage.output_tokens * p.output_per_mtok) / 1_000_000

    def ceiling(self, model: str, prompt_chars: int, max_output_tokens: int) -> float:
        """Upper-bound cost of one call, used before the call. ~1 token per 2 chars is conservative for code and Vietnamese."""
        return self.cost(model, Usage(input_tokens=math.ceil(prompt_chars / 2), output_tokens=max_output_tokens))


class BudgetLedger(BaseModel):
    """Spend per agent and per squad. Checked before every metered call; recorded after."""

    squad_limit_usd: float
    agent_limits_usd: dict[str, float]
    spent_usd: dict[str, float] = Field(default_factory=dict)

    @property
    def squad_spent(self) -> float:
        return sum(self.spent_usd.values())

    def check(self, agent: str, ceiling: float) -> None:
        if agent not in self.agent_limits_usd:
            raise BudgetExceeded(f"agent {agent} (no budget)", 0.0, 0.0, ceiling)
        spent = self.spent_usd.get(agent, 0.0)
        if spent + ceiling > self.agent_limits_usd[agent] + 1e-12:
            raise BudgetExceeded(f"agent {agent}", self.agent_limits_usd[agent], spent, ceiling)
        if self.squad_spent + ceiling > self.squad_limit_usd + 1e-12:
            raise BudgetExceeded("squad", self.squad_limit_usd, self.squad_spent, ceiling)

    def record(self, agent: str, cost: float) -> None:
        self.spent_usd[agent] = self.spent_usd.get(agent, 0.0) + cost

    @classmethod
    def for_squad(cls, squad) -> "BudgetLedger":
        agents = [squad.supervisor, *squad.members]
        return cls(squad_limit_usd=squad.budget_limit_usd, agent_limits_usd={a.role_name: a.budget_usd for a in agents})
