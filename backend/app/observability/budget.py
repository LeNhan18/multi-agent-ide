"""Cost tracking, budget circuit breaker and Langfuse observability (CLAUDE.md §2, §4.3)."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from backend.app.agents.spec import AgentSpec
from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs.state import ResourceProfile, SharedWorkspaceState, SubAgentState

MODEL_PRICING_PER_1K_TOKENS: dict[str, dict[str, float]] = {
    "claude-haiku-5-5": {"input": 0.0008, "output": 0.0040},
    "claude-sonnet-5-5": {"input": 0.0030, "output": 0.0150},
    "claude-opus-5-5": {"input": 0.0150, "output": 0.0750},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.00060},
    "gpt-5-mini": {"input": 0.00025, "output": 0.0020},
    "gpt-5.4-mini": {"input": 0.00040, "output": 0.0025},
    "gpt-5.6-terra": {"input": 0.0050, "output": 0.0200},
    "gemini-3.8-flash": {"input": 0.00020, "output": 0.00080},
    "gemini-3.5-flash-lite": {"input": 0.00010, "output": 0.00040},
    "default": {"input": 0.0030, "output": 0.0150},
}


class BudgetExceededError(RuntimeError):
    """Raised when per-agent or squad-level spending exceeds the configured ceiling."""

    def __init__(
        self,
        message: str,
        *,
        scope: str = "squad",
        agent_id: str = "",
        spent_usd: float = 0.0,
        limit_usd: float = 0.0,
        events: list[AgentEventPayload] | None = None,
    ) -> None:
        super().__init__(message)
        self.scope = scope
        self.agent_id = agent_id
        self.spent_usd = spent_usd
        self.limit_usd = limit_usd
        self.events = list(events or [])


def estimate_cost_usd(
    model: str,
    input_tokens: int,
    output_tokens: int,
    pricing: dict[str, dict[str, float]] | None = None,
) -> float:
    """Calculate estimated USD cost from input and output token counts."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("input_tokens and output_tokens must be non-negative")
    table = pricing or MODEL_PRICING_PER_1K_TOKENS
    rates = table.get(model) or table.get("default") or MODEL_PRICING_PER_1K_TOKENS["default"]
    cost = (input_tokens / 1000.0) * rates["input"] + (output_tokens / 1000.0) * rates["output"]
    return round(cost, 8)


class BudgetCircuitBreaker:
    """Tracks token/resource usage and trips when agent or squad budgets are exceeded."""

    def __init__(
        self,
        pricing: dict[str, dict[str, float]] | None = None,
        on_event: Callable[[AgentEventPayload], Any] | None = None,
    ) -> None:
        self._pricing = pricing or MODEL_PRICING_PER_1K_TOKENS
        self._on_event = on_event
        self._agent_costs: dict[str, float] = {}
        self.emitted_events: list[AgentEventPayload] = []

    def get_agent_cost(self, agent_id: str) -> float:
        return self._agent_costs.get(agent_id, 0.0)

    def reset(self, agent_id: str | None = None) -> None:
        if agent_id is None:
            self._agent_costs.clear()
        else:
            self._agent_costs.pop(agent_id, None)

    def _emit(self, event: AgentEventPayload) -> None:
        self.emitted_events.append(event)
        if self._on_event is not None:
            self._on_event(event)

    def record_usage(
        self,
        shared: SharedWorkspaceState,
        sub_state: SubAgentState,
        spec: AgentSpec,
        input_tokens: int,
        output_tokens: int,
    ) -> list[AgentEventPayload]:
        """Update token/cost counters, emit TOKEN_USAGE_UPDATE, and trip on budget breach."""
        call_cost = estimate_cost_usd(spec.llm.model, input_tokens, output_tokens, self._pricing)
        sub_state.token_count += input_tokens + output_tokens
        shared.accumulated_cost_usd = round(shared.accumulated_cost_usd + call_cost, 8)

        agent_key = sub_state.agent_id or spec.role_name
        agent_cost = round(self._agent_costs.get(agent_key, 0.0) + call_cost, 8)
        self._agent_costs[agent_key] = agent_cost

        agent_exceeded = agent_cost > spec.budget_usd
        squad_exceeded = shared.accumulated_cost_usd > shared.budget_limit_usd
        exceeded_scope: str | None = None
        if agent_exceeded and squad_exceeded:
            exceeded_scope = "agent_and_squad"
        elif agent_exceeded:
            exceeded_scope = "agent"
        elif squad_exceeded:
            exceeded_scope = "squad"

        event = AgentEventPayload(
            event_type=EventType.TOKEN_USAGE_UPDATE,
            agent_id=sub_state.agent_id,
            task_id=sub_state.task_id,
            payload={
                "model": spec.llm.model,
                "provider": spec.llm.provider,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "agent_token_count": sub_state.token_count,
                "cost_usd": call_cost,
                "agent_cost_usd": agent_cost,
                "agent_budget_usd": spec.budget_usd,
                "accumulated_cost_usd": shared.accumulated_cost_usd,
                "budget_limit_usd": shared.budget_limit_usd,
                "budget_exceeded": exceeded_scope is not None,
                "exceeded_scope": exceeded_scope,
            },
        )
        self._emit(event)

        if exceeded_scope is not None:
            if agent_exceeded:
                raise BudgetExceededError(
                    f"Agent '{agent_key}' exceeded budget (${agent_cost:.6f} > ${spec.budget_usd:.6f})",
                    scope=exceeded_scope,
                    agent_id=agent_key,
                    spent_usd=agent_cost,
                    limit_usd=spec.budget_usd,
                    events=[event],
                )
            raise BudgetExceededError(
                f"Squad exceeded budget (${shared.accumulated_cost_usd:.6f} > ${shared.budget_limit_usd:.6f})",
                scope=exceeded_scope,
                agent_id=agent_key,
                spent_usd=shared.accumulated_cost_usd,
                limit_usd=shared.budget_limit_usd,
                events=[event],
            )

        return [event]

    def record_resource_usage(
        self,
        agent_id: str,
        task_id: str,
        profile: ResourceProfile,
        mem_used_mb: float,
        cpu_percent: float,
    ) -> AgentEventPayload:
        """Emit a RESOURCE_USAGE_UPDATE event against an agent's ResourceProfile."""
        if mem_used_mb < 0 or cpu_percent < 0:
            raise ValueError("mem_used_mb and cpu_percent must be non-negative")
        mem_utilization = (
            round(float(mem_used_mb) / profile.mem_limit_mb, 4)
            if profile.mem_limit_mb > 0
            else 0.0
        )
        event = AgentEventPayload(
            event_type=EventType.RESOURCE_USAGE_UPDATE,
            agent_id=agent_id,
            task_id=task_id,
            payload={
                "mem_used_mb": round(float(mem_used_mb), 2),
                "mem_limit_mb": profile.mem_limit_mb,
                "mem_utilization": mem_utilization,
                "cpu_percent": round(float(cpu_percent), 2),
                "cpu_cores": profile.cpu_cores,
                "gpu_ids": list(profile.gpu_ids),
                "max_vram_mb": profile.max_vram_mb,
                "network_enabled": profile.network_enabled,
                "over_mem_limit": float(mem_used_mb) > profile.mem_limit_mb,
            },
        )
        self._emit(event)
        return event


class LangfuseTracer:
    """Safe Langfuse wrapper that degrades cleanly to in-memory spans offline."""

    def __init__(
        self,
        public_key: str | None = None,
        secret_key: str | None = None,
        host: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.spans: list[dict[str, Any]] = []
        self._client: Any | None = client
        self.enabled: bool = client is not None

        if self._client is None:
            pk = (
                public_key
                if public_key is not None
                else os.environ.get("LANGFUSE_PUBLIC_KEY", "")
            ).strip()
            sk = (
                secret_key
                if secret_key is not None
                else os.environ.get("LANGFUSE_SECRET_KEY", "")
            ).strip()
            resolved_host = (
                host
                if host is not None
                else (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "")
            ).strip() or None

            if pk and sk:
                try:
                    from langfuse import Langfuse

                    kwargs: dict[str, Any] = {"public_key": pk, "secret_key": sk}
                    if resolved_host:
                        kwargs["base_url"] = resolved_host
                    self._client = Langfuse(**kwargs)
                    self.enabled = True
                except Exception:
                    self._client = None
                    self.enabled = False

    @property
    def is_offline(self) -> bool:
        return self._client is None

    def trace_step(
        self,
        trace_id: str,
        agent_id: str,
        name: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record one agent execution step to Langfuse or the in-memory span log."""
        step_metadata = dict(metadata or {})
        span_record: dict[str, Any] = {
            "span_id": uuid.uuid4().hex,
            "trace_id": trace_id,
            "agent_id": agent_id,
            "name": name,
            "metadata": step_metadata,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "langfuse" if self._client is not None else "in_memory",
        }

        if self._client is not None:
            try:
                if hasattr(self._client, "start_observation"):
                    otel_trace_id = (
                        self._client.create_trace_id(seed=trace_id)
                        if hasattr(self._client, "create_trace_id")
                        else trace_id
                    )
                    obs = self._client.start_observation(
                        trace_context={"trace_id": otel_trace_id},
                        name=name,
                        as_type="span",
                        metadata={"agent_id": agent_id, **step_metadata},
                    )
                    if hasattr(obs, "end"):
                        obs.end()
                    span_record["otel_trace_id"] = otel_trace_id
                elif hasattr(self._client, "trace"):
                    trace_obj = self._client.trace(
                        id=trace_id,
                        name=name,
                        metadata={"agent_id": agent_id},
                    )
                    if hasattr(trace_obj, "span"):
                        trace_obj.span(
                            name=name,
                            metadata={"agent_id": agent_id, **step_metadata},
                        )
            except Exception as exc:
                span_record["mode"] = "in_memory_fallback"
                span_record["langfuse_error"] = str(exc)

        self.spans.append(span_record)
        return span_record

    def get_spans(
        self,
        trace_id: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            span
            for span in self.spans
            if (trace_id is None or span["trace_id"] == trace_id)
            and (agent_id is None or span["agent_id"] == agent_id)
        ]

    def flush(self) -> None:
        if self._client is not None and hasattr(self._client, "flush"):
            try:
                self._client.flush()
            except Exception:
                pass

    def clear(self) -> None:
        self.spans.clear()
