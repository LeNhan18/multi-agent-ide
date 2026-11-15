"""REST API endpoints for LangGraph squad orchestration."""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from backend.app.agents.spec import AgentSpec, SquadTemplate, load_all_squads
from backend.app.graphs.orchestrator import SquadOrchestrator

_DEFAULT_TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"
TEMPLATES_DIR: Path = _DEFAULT_TEMPLATES_DIR

router = APIRouter(prefix="/api", tags=["Orchestration"])
orchestration_router = router

_RUNS: dict[str, dict[str, Any]] = {}
_RUNS_LOCK = threading.RLock()


class RunRequest(BaseModel):
    """Payload to start a squad orchestration run."""

    squad_name: str = Field(..., min_length=1)
    goal: str = Field(..., min_length=1)
    workspace_root: str = "."
    thread_id: str | None = None
    requested_tools: dict[str, list[str]] | None = None


class ResumeRequest(BaseModel):
    """Payload to approve or reject a paused HITL interrupt."""

    approved: bool
    feedback: str | None = None


class RunResponse(BaseModel):
    """Serialized orchestration run result."""

    thread_id: str
    squad_name: str
    status: str
    shared: dict[str, Any] = Field(default_factory=dict)
    sub_agents: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_hitl: dict[str, Any] | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    final_output: str | None = None


class RunStateResponse(RunResponse):
    """Current orchestration state including LangGraph interrupt metadata."""

    is_interrupted: bool = False
    next: list[str] = Field(default_factory=list)


def get_templates_dir() -> Path:
    """Resolve the active templates directory, honoring module or env overrides."""
    if TEMPLATES_DIR != _DEFAULT_TEMPLATES_DIR:
        return Path(TEMPLATES_DIR)
    env_dir = os.environ.get("TEMPLATES_DIR") or os.environ.get("SQUAD_TEMPLATES_DIR")
    if env_dir:
        return Path(env_dir)
    return Path(TEMPLATES_DIR)


def _load_squads_map() -> dict[str, SquadTemplate]:
    templates_dir = get_templates_dir()
    if not templates_dir.exists() or not templates_dir.is_dir():
        return {}
    return load_all_squads(templates_dir)


def _get_squad_or_404(squad_name: str) -> SquadTemplate:
    squads = _load_squads_map()
    squad = squads.get(squad_name)
    if squad is None:
        raise HTTPException(status_code=404, detail=f"Squad '{squad_name}' not found")
    return squad


def _validate_workspace_root(workspace_root: str) -> Path:
    if not workspace_root or not workspace_root.strip():
        raise HTTPException(status_code=400, detail="workspace_root must not be empty")
    try:
        resolved = Path(workspace_root).expanduser().resolve()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid workspace_root: {exc}") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise HTTPException(
            status_code=400,
            detail=f"workspace_root does not exist or is not a directory: {workspace_root}",
        )
    return resolved


def _get_run_or_404(thread_id: str) -> dict[str, Any]:
    with _RUNS_LOCK:
        record = _RUNS.get(thread_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{thread_id}' not found")
    return record


def _serialize_agent(agent: AgentSpec) -> dict[str, Any]:
    return agent.model_dump(mode="json")


def _serialize_squad(squad: SquadTemplate) -> dict[str, Any]:
    return {
        "name": squad.name,
        "domain": squad.domain,
        "description": squad.description,
        "budget_limit_usd": squad.budget_limit_usd,
        "max_steps": squad.max_steps,
        "supervisor": _serialize_agent(squad.supervisor),
        "members": [_serialize_agent(member) for member in squad.members],
    }


def _state_snapshot_to_response(
    thread_id: str,
    squad_name: str,
    snapshot: dict[str, Any],
    fallback_values: dict[str, Any] | None = None,
) -> RunStateResponse:
    values = snapshot.get("values")
    if not isinstance(values, dict) or not values:
        values = fallback_values or {}
    next_nodes = [str(node) for node in snapshot.get("next", [])]
    is_interrupted = bool(snapshot.get("is_interrupted", bool(next_nodes)))
    return RunStateResponse(
        thread_id=thread_id,
        squad_name=squad_name,
        is_interrupted=is_interrupted,
        next=next_nodes,
        status=str(values.get("status", "")),
        shared=dict(values.get("shared") or {}),
        sub_agents=dict(values.get("sub_agents") or {}),
        pending_hitl=values.get("pending_hitl"),
        events=list(values.get("events") or []),
        final_output=values.get("final_output"),
    )


@router.get("/squads")
def list_squads() -> dict[str, list[dict[str, Any]]]:
    """List all configured squad templates with summary metadata."""
    squads = _load_squads_map()
    return {"squads": [_serialize_squad(squad) for squad in squads.values()]}


@router.get("/squads/{squad_name}/graph")
def get_squad_graph(squad_name: str) -> dict[str, Any]:
    """Return compiled LangGraph topology and Mermaid diagram for a squad."""
    squad = _get_squad_or_404(squad_name)
    orchestrator = SquadOrchestrator(squad)
    topology = orchestrator.get_graph_topology()
    return {
        "squad_name": squad.name,
        "nodes": list(topology.get("nodes", [])),
        "edges": list(topology.get("edges", [])),
        "interrupt_before": list(topology.get("interrupt_before", [])),
        "mermaid": str(topology.get("mermaid", "")),
    }


@router.post("/runs", response_model=RunResponse)
def create_run(req: RunRequest) -> RunResponse:
    """Start a new squad orchestration run on a validated workspace directory."""
    squad = _get_squad_or_404(req.squad_name)
    resolved_root = _validate_workspace_root(req.workspace_root)
    thread_id = req.thread_id.strip() if req.thread_id and req.thread_id.strip() else uuid.uuid4().hex
    orchestrator = SquadOrchestrator(squad)
    with _RUNS_LOCK:
        _RUNS[thread_id] = {
            "thread_id": thread_id,
            "squad_name": squad.name,
            "orchestrator": orchestrator,
        }
    try:
        state = orchestrator.invoke(
            goal=req.goal,
            workspace_root=str(resolved_root),
            thread_id=thread_id,
            requested_tools=req.requested_tools,
        )
    except Exception:
        with _RUNS_LOCK:
            _RUNS.pop(thread_id, None)
        raise

    return RunResponse(
        thread_id=thread_id,
        squad_name=squad.name,
        status=str(state.get("status", "")),
        shared=dict(state.get("shared") or {}),
        sub_agents=dict(state.get("sub_agents") or {}),
        pending_hitl=state.get("pending_hitl"),
        events=list(state.get("events") or []),
        final_output=state.get("final_output"),
    )


@router.get("/runs/{thread_id}", response_model=RunStateResponse)
def get_run(thread_id: str) -> RunStateResponse:
    """Fetch the latest checkpointed state for an existing run."""
    record = _get_run_or_404(thread_id)
    snapshot = record["orchestrator"].get_state(thread_id)
    return _state_snapshot_to_response(thread_id, record["squad_name"], snapshot)


@router.post("/runs/{thread_id}/resume", response_model=RunStateResponse)
def resume_run(thread_id: str, req: ResumeRequest) -> RunStateResponse:
    """Resume an interrupted run with a human approval or rejection decision."""
    record = _get_run_or_404(thread_id)
    orchestrator = record["orchestrator"]
    try:
        resumed = orchestrator.resume(thread_id, req.approved, req.feedback)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    snapshot = orchestrator.get_state(thread_id)
    fallback = resumed.get("values") if isinstance(resumed, dict) and "values" in resumed else resumed
    return _state_snapshot_to_response(
        thread_id,
        record["squad_name"],
        snapshot,
        fallback_values=fallback if isinstance(fallback, dict) else None,
    )
