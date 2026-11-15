"""Unit tests for the LangGraph orchestration REST API and FastAPI app integration."""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.routing import APIRoute, APIWebSocketRoute
from pydantic import ValidationError

from backend.app import main
from backend.app.api import orchestration
from backend.app.graphs.state import TaskStatus


class OrchestrationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        with orchestration._RUNS_LOCK:
            orchestration._RUNS.clear()
        self.tmp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        with orchestration._RUNS_LOCK:
            orchestration._RUNS.clear()
        self.tmp_dir.cleanup()

    def test_main_app_routes_and_allowed_origins(self) -> None:
        self.assertTrue(
            {
                "http://localhost:3000",
                "http://127.0.0.1:3000",
                "http://localhost:8080",
                "http://127.0.0.1:8080",
            }.issubset(main.ALLOWED_ORIGINS)
        )

        def collect_routes(routes: list[object]) -> tuple[dict[str, set[str]], set[str]]:
            found_http: dict[str, set[str]] = {}
            found_ws: set[str] = set()
            for route in routes:
                if isinstance(route, APIRoute):
                    found_http.setdefault(route.path, set()).update(route.methods or set())
                elif isinstance(route, APIWebSocketRoute):
                    found_ws.add(route.path)
                elif hasattr(route, "original_router"):
                    nested_http, nested_ws = collect_routes(route.original_router.routes)
                    for path, methods in nested_http.items():
                        found_http.setdefault(path, set()).update(methods)
                    found_ws.update(nested_ws)
            return found_http, found_ws

        http_routes, ws_paths = collect_routes(main.app.routes)

        self.assertIn("GET", http_routes.get("/health", set()))
        self.assertIn("POST", http_routes.get("/mcp/{service}", set()))
        self.assertIn("/ws/events", ws_paths)

        self.assertIn("GET", http_routes.get("/api/squads", set()))
        self.assertIn("GET", http_routes.get("/api/squads/{squad_name}/graph", set()))
        self.assertIn("POST", http_routes.get("/api/runs", set()))
        self.assertIn("GET", http_routes.get("/api/runs/{thread_id}", set()))
        self.assertIn("POST", http_routes.get("/api/runs/{thread_id}/resume", set()))

        schema = main.app.openapi()
        for path in (
            "/health",
            "/mcp/{service}",
            "/api/squads",
            "/api/squads/{squad_name}/graph",
            "/api/runs",
            "/api/runs/{thread_id}",
            "/api/runs/{thread_id}/resume",
        ):
            self.assertIn(path, schema["paths"])

    def test_list_squads_and_template_dir_overrides(self) -> None:
        result = orchestration.list_squads()
        self.assertIn("squads", result)
        by_name = {item["name"]: item for item in result["squads"]}
        self.assertTrue(
            {
                "engineering-squad",
                "legal-audit-squad",
                "market-research-squad",
                "data-finance-squad",
                "content-marketing-squad",
            }.issubset(by_name)
        )
        eng = by_name["engineering-squad"]
        for key in (
            "name",
            "domain",
            "description",
            "budget_limit_usd",
            "max_steps",
            "supervisor",
            "members",
        ):
            self.assertIn(key, eng)
        self.assertEqual(eng["supervisor"]["role_name"], "supervisor")
        self.assertGreaterEqual(len(eng["members"]), 2)

        with tempfile.TemporaryDirectory() as empty_dir:
            with patch.object(orchestration, "TEMPLATES_DIR", Path(empty_dir)):
                self.assertEqual(orchestration.list_squads(), {"squads": []})

            custom_yaml = Path(empty_dir) / "custom.yaml"
            custom_yaml.write_text(
                "\n".join(
                    [
                        "name: custom-test-squad",
                        "domain: custom",
                        "description: Test squad via env override",
                        "supervisor:",
                        "  role_name: lead",
                        "  system_prompt: Coordinate work",
                        "members:",
                        "  - role_name: worker",
                        "    system_prompt: Execute task",
                        "  - role_name: checker",
                        "    system_prompt: Check output",
                        "    is_reviewer: true",
                        "    reviews: [worker]",
                        "    model_config:",
                        "      provider: openai",
                        "      model: gpt-5.4-mini",
                    ]
                ),
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"TEMPLATES_DIR": empty_dir}):
                env_result = orchestration.list_squads()
                self.assertEqual([s["name"] for s in env_result["squads"]], ["custom-test-squad"])

    def test_get_squad_graph_and_404(self) -> None:
        graph = orchestration.get_squad_graph("engineering-squad")
        self.assertIn("nodes", graph)
        self.assertIn("edges", graph)
        self.assertIn("interrupt_before", graph)
        self.assertIn("hitl_gate", graph["interrupt_before"])
        self.assertIsInstance(graph["mermaid"], str)
        self.assertTrue(graph["mermaid"].strip())
        self.assertIn("supervisor", graph["nodes"])
        self.assertIn("coder", graph["nodes"])
        self.assertIn("reviewer", graph["nodes"])

        with self.assertRaises(HTTPException) as ctx:
            orchestration.get_squad_graph("nonexistent-squad")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_create_run_to_done_and_get_state(self) -> None:
        req = orchestration.RunRequest(
            squad_name="engineering-squad",
            goal="Implement health check test",
            workspace_root=self.tmp_dir.name,
        )
        run = orchestration.create_run(req)
        self.assertEqual(run.squad_name, "engineering-squad")
        self.assertEqual(run.status, TaskStatus.DONE.value)
        self.assertTrue(run.thread_id)
        self.assertIsNone(run.pending_hitl)
        self.assertTrue(run.final_output)
        self.assertIn(run.thread_id, orchestration._RUNS)
        self.assertGreater(len(run.events), 0)

        state = orchestration.get_run(run.thread_id)
        self.assertEqual(state.thread_id, run.thread_id)
        self.assertEqual(state.squad_name, "engineering-squad")
        self.assertFalse(state.is_interrupted)
        self.assertEqual(state.next, [])
        self.assertEqual(state.status, TaskStatus.DONE.value)
        self.assertEqual(state.final_output, run.final_output)

        # Resuming an already completed (non-interrupted) run -> 409
        with self.assertRaises(HTTPException) as not_interrupted_ctx:
            orchestration.resume_run(
                run.thread_id,
                orchestration.ResumeRequest(approved=True),
            )
        self.assertEqual(not_interrupted_ctx.exception.status_code, 409)

    def test_hitl_interrupt_then_resume_done_and_reject(self) -> None:
        approve_req = orchestration.RunRequest(
            squad_name="engineering-squad",
            goal="Clean temporary artifact",
            workspace_root=self.tmp_dir.name,
            thread_id="hitl-approve-thread",
            requested_tools={"coder": ["delete_file"]},
        )
        paused = orchestration.create_run(approve_req)
        self.assertEqual(paused.thread_id, "hitl-approve-thread")
        self.assertEqual(paused.status, TaskStatus.AWAITING_APPROVAL.value)
        self.assertIsNotNone(paused.pending_hitl)
        self.assertEqual(paused.pending_hitl["agent_id"], "coder")
        self.assertEqual(paused.pending_hitl["tool"], "delete_file")

        mid_state = orchestration.get_run("hitl-approve-thread")
        self.assertTrue(mid_state.is_interrupted)
        self.assertIn("hitl_gate", mid_state.next)
        self.assertEqual(mid_state.status, TaskStatus.AWAITING_APPROVAL.value)

        resumed = orchestration.resume_run(
            "hitl-approve-thread",
            orchestration.ResumeRequest(approved=True, feedback="Safe to delete"),
        )
        self.assertFalse(resumed.is_interrupted)
        self.assertEqual(resumed.next, [])
        self.assertEqual(resumed.status, TaskStatus.DONE.value)
        self.assertIsNone(resumed.pending_hitl)
        self.assertTrue(resumed.final_output)

        reject_req = orchestration.RunRequest(
            squad_name="engineering-squad",
            goal="Attempt sensitive operation",
            workspace_root=self.tmp_dir.name,
            thread_id="hitl-reject-thread",
            requested_tools={"coder": ["install_system_package"]},
        )
        paused_reject = orchestration.create_run(reject_req)
        self.assertEqual(paused_reject.status, TaskStatus.AWAITING_APPROVAL.value)

        rejected = orchestration.resume_run(
            "hitl-reject-thread",
            orchestration.ResumeRequest(approved=False, feedback="Not permitted"),
        )
        self.assertFalse(rejected.is_interrupted)
        self.assertEqual(rejected.status, TaskStatus.NEEDS_HUMAN_HELP.value)
        self.assertIsNone(rejected.pending_hitl)

    def test_validation_and_404_errors(self) -> None:
        # Missing workspace_root directory -> 400
        with self.assertRaises(HTTPException) as missing_ws_ctx:
            orchestration.create_run(
                orchestration.RunRequest(
                    squad_name="engineering-squad",
                    goal="Run on missing directory",
                    workspace_root=str(Path(self.tmp_dir.name) / "does-not-exist"),
                )
            )
        self.assertEqual(missing_ws_ctx.exception.status_code, 400)

        # File passed as workspace_root -> 400
        file_path = Path(self.tmp_dir.name) / "not_a_dir.txt"
        file_path.write_text("hello", encoding="utf-8")
        with self.assertRaises(HTTPException) as file_ws_ctx:
            orchestration.create_run(
                orchestration.RunRequest(
                    squad_name="engineering-squad",
                    goal="Run on file instead of dir",
                    workspace_root=str(file_path),
                )
            )
        self.assertEqual(file_ws_ctx.exception.status_code, 400)

        # Unknown squad -> 404
        with self.assertRaises(HTTPException) as squad_ctx:
            orchestration.create_run(
                orchestration.RunRequest(
                    squad_name="unknown-squad",
                    goal="Should fail",
                    workspace_root=self.tmp_dir.name,
                )
            )
        self.assertEqual(squad_ctx.exception.status_code, 404)

        # Unknown thread_id on GET /api/runs/{thread_id} and POST /api/runs/{thread_id}/resume -> 404
        with self.assertRaises(HTTPException) as get_run_ctx:
            orchestration.get_run("missing-thread-id")
        self.assertEqual(get_run_ctx.exception.status_code, 404)

        with self.assertRaises(HTTPException) as resume_ctx:
            orchestration.resume_run(
                "missing-thread-id",
                orchestration.ResumeRequest(approved=True),
            )
        self.assertEqual(resume_ctx.exception.status_code, 404)

        # Pydantic request schema validation
        with self.assertRaises(ValidationError):
            orchestration.RunRequest(squad_name="", goal="Test")


if __name__ == "__main__":
    unittest.main()
