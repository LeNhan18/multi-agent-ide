"""Unit tests for the LangGraph SquadOrchestrator (synchronous for Windows sandbox)."""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from backend.app.agents.spec import AgentSpec, SquadTemplate, load_all_squads
from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs import OrchestratorState, SquadOrchestrator, create_checkpointer
from backend.app.graphs.state import SharedWorkspaceState, SubAgentState, TaskStatus

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "templates"


def _build_custom_squad(
    *,
    supervisor_role: str = "director",
    worker_role: str = "analyst",
    reviewer_role: str = "qa_verifier",
    budget_limit_usd: float = 5.0,
    max_steps: int = 100,
    max_review_rejections: int = 3,
    context_window: int = 200_000,
    hitl_tools: list[str] | None = None,
) -> SquadTemplate:
    return SquadTemplate.model_validate(
        {
            "name": "custom-test-squad",
            "domain": "custom",
            "description": "Custom squad for verifying zero hardcoded roles.",
            "budget_limit_usd": budget_limit_usd,
            "max_steps": max_steps,
            "max_review_rejections": max_review_rejections,
            "supervisor": {
                "role_name": supervisor_role,
                "system_prompt": "Coordinate tasks.",
                "model_config": {"provider": "anthropic", "model": "claude-haiku-5-5"},
            },
            "members": [
                {
                    "role_name": worker_role,
                    "system_prompt": "Execute tasks.",
                    "allowed_mcp_tools": ["read_file_lines", "write_note"],
                    "hitl_tools": hitl_tools or ["dangerous_action"],
                    "model_config": {
                        "provider": "anthropic",
                        "model": "claude-sonnet-5-5",
                        "context_window": context_window,
                    },
                },
                {
                    "role_name": reviewer_role,
                    "system_prompt": "Verify work.",
                    "is_reviewer": True,
                    "reviews": [worker_role],
                    "model_config": {"provider": "openai", "model": "gpt-4o-mini"},
                },
            ],
        }
    )


class TestLangGraphOrchestrator(unittest.TestCase):
    def test_compile_all_templates_and_topology(self) -> None:
        squads = load_all_squads(TEMPLATES_DIR)
        self.assertGreaterEqual(len(squads), 3)

        for squad_name, squad in squads.items():
            with self.subTest(squad=squad_name):
                orch = SquadOrchestrator(squad)
                topology = orch.get_graph_topology()

                self.assertEqual(topology["interrupt_before"], ["hitl_gate"])
                self.assertIn("hitl_gate", topology["nodes"])
                self.assertIn(squad.supervisor.role_name, topology["nodes"])
                for member in squad.members:
                    self.assertIn(member.role_name, topology["nodes"])

                edges = {(e["source"], e["target"]) for e in topology["edges"]}
                self.assertIn(("__start__", squad.supervisor.role_name), edges)
                for member in squad.members:
                    if not member.is_reviewer:
                        self.assertIn((squad.supervisor.role_name, member.role_name), edges)
                        self.assertIn((member.role_name, "hitl_gate"), edges)
                        self.assertIn(("hitl_gate", member.role_name), edges)
                    else:
                        self.assertIn((member.role_name, squad.supervisor.role_name), edges)
                        for reviewed in member.reviews:
                            self.assertIn((member.role_name, reviewed), edges)

                mermaid = topology["mermaid"]
                self.assertIsInstance(mermaid, str)
                self.assertIn("graph TD", mermaid)
                self.assertIn("hitl_gate", mermaid)
                self.assertIn("__interrupt = before", mermaid)

    def test_happy_path_single_and_multi_worker_squads(self) -> None:
        squads = load_all_squads(TEMPLATES_DIR)

        eng = squads["engineering-squad"]
        orch = SquadOrchestrator(eng)
        result = orch.invoke(
            goal="Implement parser module",
            workspace_root=".",
            thread_id="eng-happy-1",
        )
        self.assertEqual(result["status"], TaskStatus.DONE.value)
        self.assertIsNotNone(result["final_output"])
        self.assertIsNone(result["pending_hitl"])
        self.assertEqual(len(result["shared"]["tasks"]), 1)
        self.assertEqual(result["shared"]["tasks"][0]["status"], TaskStatus.DONE.value)

        SharedWorkspaceState.model_validate(result["shared"])
        for sub_dict in result["sub_agents"].values():
            SubAgentState.model_validate(sub_dict)
        for ev_dict in result["events"]:
            AgentEventPayload.model_validate(ev_dict)

        event_types = [e["event_type"] for e in result["events"]]
        self.assertIn(EventType.AGENT_STATE_CHANGE.value, event_types)
        self.assertIn(EventType.TOOL_CALL_START.value, event_types)
        self.assertIn(EventType.TOOL_CALL_RESULT.value, event_types)

        research = squads["market-research-squad"]
        research_orch = SquadOrchestrator(research)
        res_multi = research_orch.invoke(
            goal="Analyze 2026 enterprise AI adoption",
            workspace_root=".",
            thread_id="research-happy-1",
        )
        self.assertEqual(res_multi["status"], TaskStatus.DONE.value)
        self.assertEqual(len(res_multi["shared"]["tasks"]), 2)
        self.assertTrue(
            all(t["status"] == TaskStatus.DONE.value for t in res_multi["shared"]["tasks"])
        )

    def test_zero_hardcoded_roles_custom_squad(self) -> None:
        custom = _build_custom_squad(
            supervisor_role="lead_coordinator",
            worker_role="field_specialist",
            reviewer_role="compliance_checker",
        )
        orch = SquadOrchestrator(custom)
        result = orch.invoke(goal="Audit custom ledger", thread_id="custom-role-1")
        self.assertEqual(result["status"], TaskStatus.DONE.value)
        self.assertEqual(result["shared"]["tasks"][0]["assigned_agent"], "field_specialist")
        self.assertTrue(result["review_decisions"]["field_specialist"]["approved"])

    def test_reviewer_reject_then_worker_fixes_then_approve(self) -> None:
        custom = _build_custom_squad()
        worker_attempts = {"count": 0}
        review_attempts = {"count": 0}

        def worker_handler(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            worker_attempts["count"] += 1
            if worker_attempts["count"] == 1:
                return {"output": "Draft v1 missing citations", "tool": "write_note"}
            return {"output": "Draft v2 with verified citations", "tool": "write_note"}

        def reviewer_handler(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            review_attempts["count"] += 1
            if review_attempts["count"] == 1:
                return {"approved": False, "feedback": "Missing primary source citations"}
            return {"approved": True, "feedback": "Citations verified"}

        orch = SquadOrchestrator(
            custom,
            agent_handlers={"analyst": worker_handler, "qa_verifier": reviewer_handler},
        )
        result = orch.invoke(goal="Prepare quarterly brief", thread_id="reject-then-approve")

        self.assertEqual(worker_attempts["count"], 2)
        self.assertEqual(review_attempts["count"], 2)
        self.assertEqual(result["status"], TaskStatus.DONE.value)
        self.assertEqual(result["review_counts"]["analyst"], 1)
        self.assertTrue(result["review_decisions"]["analyst"]["approved"])
        self.assertIsNone(result["sub_agents"]["analyst"]["current_error"])
        self.assertEqual(result["sub_agents"]["analyst"]["error_signature_attempts"], {})

    def test_reviewer_circuit_breaker_on_max_rejections(self) -> None:
        custom = _build_custom_squad(max_review_rejections=3)
        reasons = ["issue_alpha", "issue_beta", "issue_gamma"]

        def always_reject(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            idx = state.get("review_counts", {}).get("analyst", 0)
            return {"approved": False, "feedback": reasons[idx % len(reasons)]}

        orch = SquadOrchestrator(custom, agent_handlers={"qa_verifier": always_reject})
        result = orch.invoke(goal="Failing task", thread_id="max-rejections-1")

        self.assertEqual(result["status"], TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertEqual(result["review_counts"]["analyst"], 3)
        self.assertFalse(result["review_decisions"]["analyst"]["approved"])
        self.assertEqual(
            result["shared"]["tasks"][0]["status"], TaskStatus.NEEDS_HUMAN_HELP.value
        )

    def test_reviewer_circuit_breaker_on_same_error_attempts(self) -> None:
        custom = _build_custom_squad(max_review_rejections=10)

        def same_error_reject(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            return {"approved": False, "feedback": "deterministic_schema_mismatch"}

        orch = SquadOrchestrator(custom, agent_handlers={"qa_verifier": same_error_reject})
        result = orch.invoke(goal="Repeated error task", thread_id="same-error-1")

        self.assertEqual(result["status"], TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertEqual(result["review_counts"]["analyst"], 3)
        self.assertEqual(
            result["sub_agents"]["analyst"]["error_signature_attempts"][
                "deterministic_schema_mismatch"
            ],
            3,
        )

    def test_hitl_interrupt_and_resume_approve_and_reject(self) -> None:
        eng = load_all_squads(TEMPLATES_DIR)["engineering-squad"]
        orch = SquadOrchestrator(eng)

        # Branch 1: interrupt then approve -> DONE
        paused = orch.invoke(
            goal="Clean legacy artifact",
            thread_id="hitl-approve-1",
            requested_tools={"coder": ["delete_file"]},
        )
        self.assertEqual(paused["status"], TaskStatus.AWAITING_APPROVAL.value)
        self.assertEqual(
            paused["pending_hitl"],
            {"agent_id": "coder", "task_id": paused["current_task_id"], "tool": "delete_file"},
        )
        paused_event_types = [e["event_type"] for e in paused["events"]]
        self.assertIn(EventType.HITL_INTERRUPT_REQUIRED.value, paused_event_types)
        self.assertNotIn(EventType.TOOL_CALL_START.value, paused_event_types)

        snap = orch.get_state("hitl-approve-1")
        self.assertTrue(snap["is_interrupted"])
        self.assertEqual(snap["next"], ["hitl_gate"])

        resumed = orch.resume("hitl-approve-1", approved=True, feedback="Safe to remove")
        self.assertEqual(resumed["status"], TaskStatus.DONE.value)
        self.assertIsNone(resumed["pending_hitl"])
        self.assertIsNone(resumed["hitl_approved"])
        resumed_event_types = [e["event_type"] for e in resumed["events"]]
        self.assertIn(EventType.TOOL_CALL_START.value, resumed_event_types)
        self.assertIn(EventType.TOOL_CALL_RESULT.value, resumed_event_types)

        final_snap = orch.get_state("hitl-approve-1")
        self.assertFalse(final_snap["is_interrupted"])
        self.assertEqual(final_snap["next"], [])

        # Branch 2: interrupt then reject -> NEEDS_HUMAN_HELP
        paused_reject = orch.invoke(
            goal="Delete production config",
            thread_id="hitl-reject-1",
            requested_tools={"coder": ["delete_file"]},
        )
        self.assertEqual(paused_reject["status"], TaskStatus.AWAITING_APPROVAL.value)
        rejected = orch.resume("hitl-reject-1", approved=False, feedback="Do not delete config")
        self.assertEqual(rejected["status"], TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertIsNone(rejected["pending_hitl"])
        self.assertEqual(
            rejected["shared"]["tasks"][0]["status"], TaskStatus.NEEDS_HUMAN_HELP.value
        )
        self.assertFalse(orch.get_state("hitl-reject-1")["is_interrupted"])

    def test_budget_limit_and_max_steps_circuit_breakers(self) -> None:
        # Budget circuit breaker via handler exceeding budget_limit_usd
        low_budget = _build_custom_squad(budget_limit_usd=0.05)

        def expensive_worker(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            return {"output": "Expensive call", "cost_usd": 0.12}

        orch_budget = SquadOrchestrator(
            low_budget, agent_handlers={"analyst": expensive_worker}
        )
        res_budget = orch_budget.invoke(goal="Compute heavy job", thread_id="budget-trip-1")
        self.assertEqual(res_budget["status"], TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertGreater(res_budget["shared"]["accumulated_cost_usd"], 0.05)

        # Max steps circuit breaker
        tiny_steps = _build_custom_squad(max_steps=1)
        orch_steps = SquadOrchestrator(tiny_steps)
        res_steps = orch_steps.invoke(goal="Step capped job", thread_id="steps-trip-1")
        self.assertEqual(res_steps["status"], TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertGreater(res_steps["shared"]["step_count"], 1)

    def test_context_compaction_at_75_percent_threshold(self) -> None:
        compact_squad = _build_custom_squad(context_window=1000)

        def verbose_worker(state: OrchestratorState, spec: AgentSpec) -> dict[str, Any]:
            sub_agents = state["sub_agents"]
            sub = dict(sub_agents["analyst"])
            messages = [{"role": "user", "content": f"old turn {i}"} for i in range(10)]
            sub["messages"] = messages
            sub["token_count"] = 800  # >= 750 (75% of 1000)
            sub_agents["analyst"] = sub
            return {"sub_agents": sub_agents}

        orch = SquadOrchestrator(compact_squad, agent_handlers={"analyst": verbose_worker})
        result = orch.invoke(goal="Preserve original goal", thread_id="compact-1")

        self.assertEqual(result["status"], TaskStatus.DONE.value)
        analyst_msgs = result["sub_agents"]["analyst"]["messages"]
        self.assertEqual(len(analyst_msgs), 6)  # 1 summary message + 5 recent turns
        self.assertEqual(analyst_msgs[0]["role"], "system")
        self.assertIn("Preserve original goal", analyst_msgs[0]["content"])
        self.assertEqual(
            [m["content"] for m in analyst_msgs[1:]],
            [f"old turn {i}" for i in range(5, 10)],
        )
        self.assertLess(result["sub_agents"]["analyst"]["token_count"], 750)

    def test_checkpointer_factory_and_sqlite_execution(self) -> None:
        mem_cp = create_checkpointer("memory")
        self.assertIsInstance(mem_cp, MemorySaver)

        with self.assertRaises(ValueError):
            create_checkpointer("sqlite")
        with self.assertRaises(ValueError):
            create_checkpointer("postgres")
        with self.assertRaises(ValueError):
            create_checkpointer("postgres", "not-a-dsn")
        with self.assertRaises(ValueError):
            create_checkpointer("redis")

        sqlite_cp = create_checkpointer("sqlite", ":memory:")
        try:
            custom = _build_custom_squad()
            orch = SquadOrchestrator(custom, checkpointer=sqlite_cp)
            paused = orch.invoke(
                goal="SQLite HITL persistence",
                thread_id="sqlite-thread-1",
                requested_tools={"analyst": ["dangerous_action"]},
            )
            self.assertEqual(paused["status"], TaskStatus.AWAITING_APPROVAL.value)
            resumed = orch.resume("sqlite-thread-1", approved=True)
            self.assertEqual(resumed["status"], TaskStatus.DONE.value)
        finally:
            sqlite_cp.conn.close()

        # Lazy async checkpointers construct context managers cleanly without live DB calls
        async_sq = create_checkpointer("async_sqlite", ":memory:")
        self.assertTrue(hasattr(async_sq, "__aenter__"))
        async_pg = create_checkpointer(
            "async_postgres", "postgresql://postgres:postgres@localhost:5432/ide"
        )
        self.assertTrue(hasattr(async_pg, "__aenter__"))

    def test_async_methods_exist(self) -> None:
        orch = SquadOrchestrator(_build_custom_squad())
        self.assertTrue(inspect.iscoroutinefunction(orch.ainvoke))
        self.assertTrue(inspect.iscoroutinefunction(orch.aget_state))
        self.assertTrue(inspect.iscoroutinefunction(orch.aresume))
        self.assertIn("OrchestratorState", globals())


if __name__ == "__main__":
    unittest.main()
