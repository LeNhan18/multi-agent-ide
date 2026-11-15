"""Provider adapters for Claude, GPT and Gemini, plus a metered client with budget enforcement.

Request bodies follow ``frontend/workflow-lab-server/handler.mjs`` so both runtimes call the
providers the same way. The metered client is the only path agents use: it checks the
agent/squad budget before the call, records the real cost after it, and publishes
TOKEN_USAGE_UPDATE.
"""
from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from backend.app.agents.catalog import MODELS, PROVIDER_KEY_ENV, Provider, dedicated_key_env
from backend.app.core.bus import EventBus
from backend.app.core.events import AgentEventPayload, EventType
from backend.app.observability.cost import BudgetLedger, PriceTable, Usage

ErrorCode = Literal["missing_key", "rejected", "limited", "model_unavailable", "unavailable", "timeout", "bad_output"]


class LLMError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class LLMRequest(BaseModel):
    model: str
    system: str
    prompt: str
    json_schema: dict[str, Any] | None = None
    schema_name: str = "output"
    effort: Literal["low", "medium", "high"] | None = None
    max_output_tokens: int = Field(default=2800, ge=1, le=64_000)


class LLMResponse(BaseModel):
    model: str
    provider: Provider
    text: str
    data: Any = None
    usage: Usage
    cost_usd: float | None = None
    duration_ms: int


def resolve_key(provider: Provider, squad: str | None = None, role: str | None = None,
                dedicated: bool = False, env: Mapping[str, str] | None = None) -> str:
    """Shared key, or the dedicated per-agent key. A missing dedicated key never falls back."""
    env = os.environ if env is None else env
    name = dedicated_key_env(provider, squad or "", role or "") if dedicated else PROVIDER_KEY_ENV[provider]
    key = (env.get(name) or "").strip()
    if not key:
        raise LLMError("missing_key", f"Set {name} in the backend environment.")
    return key


def _http_error(status: int) -> LLMError:
    if status in (401, 403):
        return LLMError("rejected", "The provider rejected the API key.")
    if status == 429:
        return LLMError("limited", "Rate limit or quota reached; retry later.")
    if status in (400, 404):
        return LLMError("model_unavailable", "The model or a request parameter is not available for this key.")
    return LLMError("unavailable", f"Provider returned HTTP {status}.")


class LLMClient:
    """Raw provider calls. Prefer ``MeteredLLM`` for agent work."""

    def __init__(self, http: httpx.AsyncClient | None = None, timeout_s: float = 55.0) -> None:
        self._http = http or httpx.AsyncClient(timeout=timeout_s, follow_redirects=False)

    async def aclose(self) -> None:
        await self._http.aclose()

    def _build(self, provider: Provider, req: LLMRequest, key: str) -> tuple[str, dict[str, str], dict[str, Any]]:
        headers = {"Content-Type": "application/json"}
        if provider == "anthropic":
            headers |= {"x-api-key": key, "anthropic-version": "2023-06-01"}
            body: dict[str, Any] = {"model": req.model, "system": req.system, "max_tokens": req.max_output_tokens,
                                    "messages": [{"role": "user", "content": req.prompt}]}
            output_config: dict[str, Any] = {}
            if req.effort:
                body["thinking"] = {"type": "adaptive"}
                output_config["effort"] = req.effort
            if req.json_schema:
                output_config["format"] = {"type": "json_schema", "schema": req.json_schema}
            if output_config:
                body["output_config"] = output_config
            return "https://api.anthropic.com/v1/messages", headers, body
        if provider == "gemini":
            headers["x-goog-api-key"] = key
            config: dict[str, Any] = {"maxOutputTokens": req.max_output_tokens}
            if req.json_schema:
                config |= {"responseMimeType": "application/json", "responseJsonSchema": req.json_schema}
            if req.effort:
                config["thinkingConfig"] = {"thinkingLevel": req.effort.upper()}
            body = {"systemInstruction": {"parts": [{"text": req.system}]},
                    "contents": [{"parts": [{"text": req.prompt}]}], "generationConfig": config}
            return f"https://generativelanguage.googleapis.com/v1beta/models/{req.model}:generateContent", headers, body
        headers["Authorization"] = f"Bearer {key}"
        body = {"model": req.model, "store": False, "instructions": req.system, "input": req.prompt,
                "max_output_tokens": req.max_output_tokens}
        if req.effort:
            body["reasoning"] = {"effort": req.effort}
        if req.json_schema:
            body["text"] = {"format": {"type": "json_schema", "name": req.schema_name, "strict": True, "schema": req.json_schema}}
        return "https://api.openai.com/v1/responses", headers, body

    @staticmethod
    def _extract(provider: Provider, data: dict[str, Any]) -> tuple[str | None, Usage]:
        if provider == "anthropic":
            u = data.get("usage") or {}
            usage = Usage(input_tokens=u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
                          + u.get("cache_read_input_tokens", 0), output_tokens=u.get("output_tokens", 0))
            text = next((p.get("text") for p in data.get("content") or [] if p.get("type") == "text"), None)
            return (text if data.get("stop_reason") == "end_turn" else None), usage
        if provider == "gemini":
            u = data.get("usageMetadata") or {}
            usage = Usage(input_tokens=u.get("promptTokenCount", 0),
                          output_tokens=u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0))
            candidate = (data.get("candidates") or [{}])[0]
            if candidate.get("finishReason") != "STOP":
                return None, usage
            parts = (candidate.get("content") or {}).get("parts") or []
            return "".join(p.get("text", "") for p in parts if not p.get("thought")), usage
        u = data.get("usage") or {}
        usage = Usage(input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0))
        if data.get("status") != "completed":
            return None, usage
        text = next((c.get("text") for item in data.get("output") or [] for c in item.get("content") or []
                     if c.get("type") == "output_text"), None)
        return text, usage

    async def complete(self, req: LLMRequest, key: str) -> LLMResponse:
        if req.model not in MODELS:
            raise LLMError("model_unavailable", f"Unknown model {req.model}.")
        provider = MODELS[req.model][0]
        url, headers, body = self._build(provider, req, key)
        started = time.monotonic()
        try:
            response = await self._http.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise LLMError("timeout", "The provider did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise LLMError("unavailable", "Could not reach the provider.") from exc
        if response.status_code >= 300:
            raise _http_error(response.status_code)
        try:
            text, usage = self._extract(provider, response.json())
        except (ValueError, AttributeError, TypeError) as exc:
            raise LLMError("bad_output", "The provider returned an unreadable response.") from exc
        if not text:
            raise LLMError("bad_output", "The response was cut off or empty; raise max_output_tokens or simplify the task.")
        text = text.replace(key, "[REDACTED]")
        data = None
        if req.json_schema:
            try:
                data = json.loads(text)
            except ValueError as exc:
                raise LLMError("bad_output", "The response was not valid JSON for the requested schema.") from exc
        return LLMResponse(model=req.model, provider=provider, text=text, data=data, usage=usage,
                           duration_ms=int((time.monotonic() - started) * 1000))


class MeteredLLM:
    """Budget-enforced client: one per running squad."""

    def __init__(self, client: LLMClient, prices: PriceTable, ledger: BudgetLedger,
                 bus: EventBus | None = None, task_id: str = "-") -> None:
        self.client, self.prices, self.ledger, self.bus, self.task_id = client, prices, ledger, bus, task_id

    async def complete(self, agent: str, req: LLMRequest, key: str) -> LLMResponse:
        ceiling = self.prices.ceiling(req.model, len(req.system) + len(req.prompt), req.max_output_tokens)
        self.ledger.check(agent, ceiling)
        response = await self.client.complete(req, key)
        cost = self.prices.cost(req.model, response.usage)
        self.ledger.record(agent, cost)
        response.cost_usd = cost
        if self.bus:
            await self.bus.publish(AgentEventPayload(
                event_type=EventType.TOKEN_USAGE_UPDATE, agent_id=agent, task_id=self.task_id,
                payload={"model": req.model, "provider": response.provider,
                         "input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens,
                         "cost_usd": round(cost, 6), "agent_spent_usd": round(self.ledger.spent_usd[agent], 6),
                         "agent_limit_usd": self.ledger.agent_limits_usd[agent],
                         "squad_spent_usd": round(self.ledger.squad_spent, 6),
                         "squad_limit_usd": self.ledger.squad_limit_usd},
            ))
        return response
