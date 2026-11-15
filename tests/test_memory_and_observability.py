from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.agents.spec import AgentSpec, load_all_squads
from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs.state import (
    ResourceProfile,
    SharedWorkspaceState,
    SubAgentState,
    TaskStatus,
)
from backend.app.memory import (
    SharedBlackboard,
    compact_subagent_context,
    estimate_tokens,
    should_compact,
)
from backend.app.observability import (
    MODEL_PRICING_PER_1K_TOKENS,
    BudgetCircuitBreaker,
    BudgetExceededError,
    LangfuseTracer,
    estimate_cost_usd,
)

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


class TestContextCompaction(unittest.TestCase):
    def test_estimate_tokens_empty_and_scaling(self) -> None:
        self.assertEqual(estimate_tokens([]), 0)
        short = estimate_tokens([{"role": "user", "content": "hello"}])
        long = estimate_tokens([{"role": "user", "content": "hello " * 100}])
        self.assertGreater(short, 0)
        self.assertGreater(long, short)

    def test_should_compact_at_75_percent_threshold(self) -> None:
        context_window = 1000
        below = SubAgentState(
            agent_id="data_analyst",
            task_id="t-1",
            goal="Analyze Q4 earnings",
            messages=[{"role": "user", "content": "short"}],
            token_count=749,
        )
        self.assertFalse(should_compact(below, context_window=context_window))

        at_threshold = below.model_copy(update={"token_count": 750})
        self.assertTrue(should_compact(at_threshold, context_window=context_window))

        estimated_over = SubAgentState(
            agent_id="data_analyst",
            task_id="t-1",
            goal="Analyze Q4 earnings",
            messages=[{"role": "user", "content": "token " * 600}],
            token_count=0,
        )
        self.assertTrue(should_compact(estimated_over, context_window=context_window))

    def test_compact_preserves_goal_facts_and_5_recent_turns(self) -> None:
        older = [
            {
                "role": "user",
                "content": "Inspect report_q4.pdf and https://example.com/q4.csv for revenue.",
            },
            {
                "role": "assistant",
                "content": "Extracted revenue $12.5M, margin 18.4%, rows: 420 from report_q4.pdf.",
            },
            *[
                {
                    "role": "assistant",
                    "content": f"Detailed intermediate scratchpad note #{i} " + ("x" * 240),
                }
                for i in range(6)
            ],
        ]
        recent = [
            {"role": "user", "content": f"Recent user turn {i}"}
            if i % 2 == 0
            else {"role": "assistant", "content": f"Recent assistant turn {i}"}
            for i in range(5)
        ]
        all_messages = older + recent
        initial_tokens = estimate_tokens(all_messages)

        sub_state = SubAgentState(
            agent_id="data_analyst",
            task_id="task-q4",
            goal="Audit Q4 financial statement and reconcile EBITDA",
            messages=all_messages,
            modified_files=["analysis.py", "outputs/q4_summary.md"],
            current_error="Missing column EBITDA in sheet_2.xlsx",
            token_count=initial_tokens,
        )

        # Pick context_window so initial_tokens >= 0.75 * context_window
        context_window = initial_tokens
        compacted = compact_subagent_context(
            sub_state,
            context_window=context_window,
            threshold=0.75,
            keep_recent=5,
        )

        self.assertEqual(len(compacted.messages), 6)
        summary_msg = compacted.messages[0]
        self.assertEqual(summary_msg["role"], "system")
        summary_text = summary_msg["content"]

        self.assertIn("Audit Q4 financial statement and reconcile EBITDA", summary_text)
        self.assertIn("analysis.py", summary_text)
        self.assertIn("outputs/q4_summary.md", summary_text)
        self.assertIn("Missing column EBITDA in sheet_2.xlsx", summary_text)
        self.assertIn("report_q4.pdf", summary_text)
        self.assertIn("https://example.com/q4.csv", summary_text)
        self.assertIn("$12.5M", summary_text)
        self.assertIn("18.4%", summary_text)

        self.assertEqual(compacted.messages[1:], recent)
        self.assertEqual(compacted.token_count, estimate_tokens(compacted.messages))
        self.assertLess(compacted.token_count, initial_tokens)

    def test_compact_noop_when_under_keep_recent_or_under_threshold(self) -> None:
        few_msgs = [{"role": "user", "content": "msg " * 300} for _ in range(4)]
        sub_state = SubAgentState(
            agent_id="copywriter",
            task_id="t-2",
            goal="Draft launch post",
            messages=few_msgs,
            token_count=900,
        )
        untouched = compact_subagent_context(sub_state, context_window=1000, keep_recent=5)
        self.assertEqual(untouched.messages, few_msgs)


class TestSharedBlackboard(unittest.TestCase):
    def test_blackboard_updates_without_mixing_subagent_raw_history(self) -> None:
        bb = SharedBlackboard(workspace_root="e:/tmp/ws", budget_limit_usd=5.0)
        bb.add_architecture_decision("Use Parquet for intermediate analytical tables.")
        bb.add_architecture_decision("Use Parquet for intermediate analytical tables.")
        bb.update_directory_summary("data/raw.csv, reports/q4.md")
        bb.add_task("t-1", "Analyze Q4 CSV", assigned_agent="data_analyst")
        bb.update_task_status("t-1", TaskStatus.RUNNING)

        sub_state = SubAgentState(
            agent_id="data_analyst",
            task_id="t-1",
            goal="Analyze Q4 CSV",
            messages=[
                {"role": "user", "content": "RAW_TRACE_SECRET_BLOB_12345"},
                {"role": "assistant", "content": "Verbose HTML scrape <html>...</html>"},
            ],
            token_count=420,
        )

        bb.sync_subagent_outcome(
            sub_state,
            task_status=TaskStatus.DONE,
            verified_decision="Validated Q4 revenue growth at 14.2% YoY.",
        )

        state = bb.state
        self.assertEqual(
            state.architecture_decisions,
            [
                "Use Parquet for intermediate analytical tables.",
                "Validated Q4 revenue growth at 14.2% YoY.",
            ],
        )
        self.assertEqual(state.directory_summary, "data/raw.csv, reports/q4.md")
        self.assertEqual(len(state.tasks), 1)
        self.assertEqual(state.tasks[0].status, TaskStatus.DONE)

        serialized = str(bb.to_dict())
        self.assertNotIn("RAW_TRACE_SECRET_BLOB_12345", serialized)
        self.assertNotIn("Verbose HTML scrape", serialized)


class TestBudgetCircuitBreaker(unittest.TestCase):
    def test_pricing_table_contains_expected_models_and_fallback(self) -> None:
        expected_models = {
            "claude-sonnet-5-5",
            "claude-haiku-5-5",
            "claude-opus-5-5",
            "gpt-4o-mini",
            "gpt-5-mini",
            "gpt-5.4-mini",
            "gpt-5.6-terra",
            "gemini-3.8-flash",
            "gemini-3.5-flash-lite",
            "default",
        }
        self.assertTrue(expected_models <= set(MODEL_PRICING_PER_1K_TOKENS))
        fallback_cost = estimate_cost_usd("unknown-future-model", 1000, 1000)
        default_rates = MODEL_PRICING_PER_1K_TOKENS["default"]
        self.assertAlmostEqual(fallback_cost, default_rates["input"] + default_rates["output"])

    def test_record_usage_updates_cost_and_trips_on_agent_ceiling(self) -> None:
        captured: list[AgentEventPayload] = []
        breaker = BudgetCircuitBreaker(on_event=captured.append)
        shared = SharedWorkspaceState(workspace_root=".", budget_limit_usd=10.0)
        spec = AgentSpec.model_validate(
            {
                "role_name": "data_analyst",
                "system_prompt": "Analyze data",
                "budget_usd": 0.02,
                "model_config": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
            }
        )
        sub_state = SubAgentState(
            agent_id="data_analyst",
            task_id="t-10",
            goal="Analyze ledger",
        )

        # 1k input ($0.003) + 1k output ($0.015) = $0.018 <= $0.02
        events = breaker.record_usage(shared, sub_state, spec, input_tokens=1000, output_tokens=1000)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, EventType.TOKEN_USAGE_UPDATE)
        self.assertEqual(sub_state.token_count, 2000)
        self.assertAlmostEqual(shared.accumulated_cost_usd, 0.018)
        self.assertFalse(events[0].payload["budget_exceeded"])

        # Second call pushes agent spend to $0.036 > $0.02
        with self.assertRaises(BudgetExceededError) as ctx:
            breaker.record_usage(shared, sub_state, spec, input_tokens=1000, output_tokens=1000)

        self.assertEqual(ctx.exception.scope, "agent")
        self.assertEqual(len(ctx.exception.events), 1)
        self.assertEqual(ctx.exception.events[0].event_type, EventType.TOKEN_USAGE_UPDATE)
        self.assertTrue(ctx.exception.events[0].payload["budget_exceeded"])
        self.assertEqual(len(captured), 2)
        self.assertEqual(captured[-1].event_type, EventType.TOKEN_USAGE_UPDATE)

    def test_record_usage_trips_on_squad_ceiling(self) -> None:
        breaker = BudgetCircuitBreaker()
        shared = SharedWorkspaceState(workspace_root=".", budget_limit_usd=0.025)
        spec_a = AgentSpec.model_validate(
            {
                "role_name": "copywriter",
                "system_prompt": "Write copy",
                "budget_usd": 1.0,
                "model_config": {"provider": "anthropic", "model": "claude-sonnet-5-5"},
            }
        )
        spec_b = AgentSpec.model_validate(
            {
                "role_name": "editorial_reviewer",
                "system_prompt": "Review copy",
                "budget_usd": 1.0,
                "model_config": {"provider": "openai", "model": "gpt-5.6-terra"},
            }
        )
        sub_a = SubAgentState(agent_id="copywriter", task_id="t-1", goal="Draft")
        sub_b = SubAgentState(agent_id="editorial_reviewer", task_id="t-1", goal="Review")

        breaker.record_usage(shared, sub_a, spec_a, input_tokens=1000, output_tokens=1000)  # $0.018
        with self.assertRaises(BudgetExceededError) as ctx:
            # 1k input ($0.005) + 1k output ($0.020) = $0.025 -> total $0.043 > $0.025
            breaker.record_usage(shared, sub_b, spec_b, input_tokens=1000, output_tokens=1000)

        self.assertEqual(ctx.exception.scope, "squad")
        self.assertTrue(breaker.emitted_events[-1].payload["budget_exceeded"])
        self.assertEqual(breaker.emitted_events[-1].event_type, EventType.TOKEN_USAGE_UPDATE)

    def test_record_resource_usage(self) -> None:
        breaker = BudgetCircuitBreaker()
        profile = ResourceProfile(mem_limit_mb=2048, cpu_cores=2.0)
        event = breaker.record_resource_usage(
            agent_id="data_analyst",
            task_id="t-99",
            profile=profile,
            mem_used_mb=1024.0,
            cpu_percent=65.5,
        )
        self.assertEqual(event.event_type, EventType.RESOURCE_USAGE_UPDATE)
        self.assertEqual(event.payload["mem_used_mb"], 1024.0)
        self.assertEqual(event.payload["mem_limit_mb"], 2048)
        self.assertEqual(event.payload["cpu_percent"], 65.5)
        self.assertFalse(event.payload["over_mem_limit"])


class TestLangfuseTracer(unittest.TestCase):
    def test_offline_mode_logs_in_memory_without_network(self) -> None:
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY")
        }
        with patch.dict(os.environ, clean_env, clear=True):
            tracer = LangfuseTracer()
            self.assertTrue(tracer.is_offline)
            self.assertFalse(tracer.enabled)

            span = tracer.trace_step(
                trace_id="trace-001",
                agent_id="data_analyst",
                name="parse_financial_pdf",
                metadata={"file": "q4.pdf", "pages": 12},
            )

        self.assertEqual(span["mode"], "in_memory")
        self.assertEqual(span["trace_id"], "trace-001")
        self.assertEqual(span["agent_id"], "data_analyst")
        self.assertEqual(span["name"], "parse_financial_pdf")
        self.assertEqual(span["metadata"]["pages"], 12)
        self.assertEqual(len(tracer.spans), 1)
        self.assertEqual(len(tracer.get_spans(trace_id="trace-001")), 1)

    def test_injected_client_records_observation_and_handles_errors(self) -> None:
        class _FakeObservation:
            def __init__(self) -> None:
                self.ended = False

            def end(self) -> None:
                self.ended = True

        class _FakeClient:
            def __init__(self) -> None:
                self.calls: list[dict] = []
                self.obs = _FakeObservation()

            def create_trace_id(self, *, seed: str) -> str:
                return f"otel-{seed}"

            def start_observation(self, **kwargs):
                self.calls.append(kwargs)
                return self.obs

        fake = _FakeClient()
        tracer = LangfuseTracer(client=fake)
        self.assertFalse(tracer.is_offline)
        span = tracer.trace_step("tr-9", "copywriter", "draft_intro", {"tokens": 300})
        self.assertEqual(span["mode"], "langfuse")
        self.assertEqual(span["otel_trace_id"], "otel-tr-9")
        self.assertTrue(fake.obs.ended)
        self.assertEqual(len(fake.calls), 1)

        class _BrokenClient:
            def start_observation(self, **kwargs):
                raise ConnectionError("offline")

        resilient = LangfuseTracer(client=_BrokenClient())
        fallback_span = resilient.trace_step("tr-10", "copywriter", "retry_step")
        self.assertEqual(fallback_span["mode"], "in_memory_fallback")
        self.assertIn("offline", fallback_span["langfuse_error"])


class TestSquadTemplates(unittest.TestCase):
    def test_load_all_five_squad_templates(self) -> None:
        squads = load_all_squads(TEMPLATES_DIR)
        expected = {
            "engineering-squad",
            "legal-audit-squad",
            "market-research-squad",
            "data-finance-squad",
            "content-marketing-squad",
        }
        self.assertEqual(set(squads), expected)

        df = squads["data-finance-squad"]
        self.assertEqual(df.domain, "data_finance")
        self.assertEqual(df.supervisor.llm.provider, "anthropic")
        df_members = {m.role_name: m for m in df.members}
        self.assertEqual(df_members["data_analyst"].llm.provider, "anthropic")
        self.assertTrue(df_members["financial_auditor"].is_reviewer)
        self.assertEqual(df_members["financial_auditor"].reviews, ["data_analyst"])
        self.assertEqual(df_members["financial_auditor"].llm.provider, "openai")
        self.assertEqual(df_members["financial_auditor"].llm.model, "gpt-4o-mini")

        cm = squads["content-marketing-squad"]
        self.assertEqual(cm.domain, "content")
        self.assertEqual(cm.supervisor.llm.provider, "anthropic")
        cm_members = {m.role_name: m for m in cm.members}
        self.assertEqual(cm_members["copywriter"].llm.provider, "anthropic")
        self.assertTrue(cm_members["editorial_reviewer"].is_reviewer)
        self.assertEqual(cm_members["editorial_reviewer"].reviews, ["copywriter"])
        self.assertEqual(cm_members["editorial_reviewer"].llm.provider, "openai")
        self.assertEqual(cm_members["editorial_reviewer"].llm.model, "gpt-4o-mini")


if __name__ == "__main__":
    unittest.main()
