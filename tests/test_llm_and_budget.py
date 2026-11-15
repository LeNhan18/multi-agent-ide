import json
from pathlib import Path

import httpx
import pytest

from backend.app.agents.llm import LLMClient, LLMError, LLMRequest, MeteredLLM, resolve_key
from backend.app.agents.spec import load_squad
from backend.app.core.bus import EventBus
from backend.app.core.events import EventType
from backend.app.observability.cost import (
    BudgetExceeded,
    BudgetLedger,
    ModelPrice,
    PriceNotConfigured,
    PriceTable,
    Usage,
)

SCHEMA = {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"], "additionalProperties": False}
PRICES = PriceTable({
    "claude-sonnet-5-5": ModelPrice(input_per_mtok=3, output_per_mtok=15),
    "gpt-5.4-mini": ModelPrice(input_per_mtok=1, output_per_mtok=4),
    "gemini-3.8-flash": ModelPrice(input_per_mtok=0.5, output_per_mtok=2),
})
KEY = "sk-test-secret"

RESPONSES = {
    "api.anthropic.com": {"stop_reason": "end_turn", "content": [{"type": "text", "text": '{"title": "A"}'}],
                          "usage": {"input_tokens": 100, "cache_read_input_tokens": 20, "output_tokens": 50}},
    "api.openai.com": {"status": "completed", "output": [{"content": [{"type": "output_text", "text": '{"title": "O"}'}]}],
                       "usage": {"input_tokens": 120, "output_tokens": 40}},
    "generativelanguage.googleapis.com": {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"text": "hidden", "thought": True}, {"text": '{"title": "G"}'}]}}],
        "usageMetadata": {"promptTokenCount": 90, "candidatesTokenCount": 30, "thoughtsTokenCount": 10}},
}


def client(status=200, body=None, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body if body is not None else RESPONSES[request.url.host])
    return LLMClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.parametrize("model,title,usage", [
    ("claude-sonnet-5-5", "A", (120, 50)),
    ("gpt-5.4-mini", "O", (120, 40)),
    ("gemini-3.8-flash", "G", (90, 40)),
])
async def test_adapters_parse_structured_output_and_usage(model, title, usage):
    seen = []
    result = await client(seen=seen).complete(
        LLMRequest(model=model, system="s", prompt="p", json_schema=SCHEMA, effort="medium"), KEY)
    assert result.data == {"title": title}
    assert (result.usage.input_tokens, result.usage.output_tokens) == usage
    sent = json.loads(seen[0].content)
    assert "json" in json.dumps(sent).lower() and KEY not in json.dumps(sent)


@pytest.mark.parametrize("status,code", [(401, "rejected"), (429, "limited"), (404, "model_unavailable"), (500, "unavailable")])
async def test_http_errors_mapped(status, code):
    with pytest.raises(LLMError) as exc:
        await client(status=status, body={}).complete(LLMRequest(model="gpt-5.4-mini", system="s", prompt="p"), KEY)
    assert exc.value.code == code


async def test_truncated_output_is_bad_output():
    body = {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "{"}], "usage": {}}
    with pytest.raises(LLMError) as exc:
        await client(body=body).complete(LLMRequest(model="claude-sonnet-5-5", system="s", prompt="p"), KEY)
    assert exc.value.code == "bad_output"


async def test_key_is_redacted_from_output():
    body = {"status": "completed", "output": [{"content": [{"type": "output_text", "text": f"leak {KEY}"}]}], "usage": {}}
    result = await client(body=body).complete(LLMRequest(model="gpt-5.4-mini", system="s", prompt="p"), KEY)
    assert KEY not in result.text


def test_dedicated_key_never_falls_back():
    env = {"ANTHROPIC_API_KEY": "shared"}
    assert resolve_key("anthropic", env=env) == "shared"
    with pytest.raises(LLMError) as exc:
        resolve_key("anthropic", "engineering-squad", "coder", dedicated=True, env=env)
    assert "ANTHROPIC_API_KEY_ENGINEERING_SQUAD_CODER" in str(exc.value)


def test_price_table_requires_configured_models(tmp_path):
    file = tmp_path / "p.yaml"
    file.write_text("gpt-5.4-mini: {input_per_mtok: 1, output_per_mtok: 4}\nclaude-opus-5-5: {input_per_mtok: null, output_per_mtok: null}\n")
    table = PriceTable.load(file)
    assert table.cost("gpt-5.4-mini", Usage(input_tokens=1_000_000, output_tokens=0)) == 1
    with pytest.raises(PriceNotConfigured):
        table.cost("claude-opus-5-5", Usage())


async def test_metered_client_records_cost_and_publishes_event():
    squad = load_squad(Path(__file__).resolve().parents[1] / "templates" / "engineering.yaml")
    ledger = BudgetLedger.for_squad(squad)
    bus = EventBus()
    metered = MeteredLLM(client(), PRICES, ledger, bus=bus, task_id="t1")
    with bus.subscribe() as queue:
        result = await metered.complete("coder", LLMRequest(model="claude-sonnet-5-5", system="s", prompt="p"), KEY)
        event = queue.get_nowait()
    assert result.cost_usd == pytest.approx((120 * 3 + 50 * 15) / 1e6)
    assert ledger.spent_usd["coder"] == pytest.approx(result.cost_usd)
    assert event.event_type is EventType.TOKEN_USAGE_UPDATE and event.payload["agent_limit_usd"] == 3.0


async def test_budget_breaker_blocks_call_before_spending():
    seen = []
    ledger = BudgetLedger(squad_limit_usd=1.0, agent_limits_usd={"coder": 0.0001})
    metered = MeteredLLM(client(seen=seen), PRICES, ledger)
    with pytest.raises(BudgetExceeded):
        await metered.complete("coder", LLMRequest(model="claude-sonnet-5-5", system="s", prompt="p" * 1000), KEY)
    assert seen == [] and ledger.squad_spent == 0


async def test_unpriced_model_cannot_be_called():
    metered = MeteredLLM(client(), PriceTable({}), BudgetLedger(squad_limit_usd=1, agent_limits_usd={"coder": 1}))
    with pytest.raises(PriceNotConfigured):
        await metered.complete("coder", LLMRequest(model="gpt-5.4-mini", system="s", prompt="p"), KEY)
