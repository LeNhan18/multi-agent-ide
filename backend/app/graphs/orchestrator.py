"""Dynamic LangGraph squad orchestrator (CLAUDE.md §4.1, §4.3, §4.4)."""
from __future__ import annotations

import copy
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from backend.app.agents.spec import AgentSpec, SquadTemplate
from backend.app.core.events import AgentEventPayload, EventType
from backend.app.graphs.state import SharedWorkspaceState, SubAgentState, TaskItem, TaskStatus


class OrchestratorState(TypedDict):
    """Shared graph state flowing across supervisor, worker, reviewer, and HITL nodes."""

    shared: dict[str, Any]
    sub_agents: dict[str, dict[str, Any]]
    active_agent: str
    next_agent: str
    current_task_id: str
    review_counts: dict[str, int]
    review_decisions: dict[str, dict[str, Any]]
    pending_hitl: dict[str, Any] | None
    hitl_approved: bool | None
    requested_tools: dict[str, list[str]]
    events: list[dict[str, Any]]
    status: str
    final_output: str | None


AgentHandler = Callable[[OrchestratorState, AgentSpec], dict[str, Any]]


def create_checkpointer(
    backend: str = "memory",
    conn_string: str | None = None,
    *,
    async_mode: bool = False,
) -> Any:
    """Create a LangGraph checkpointer for memory, sqlite, or postgres backends."""
    normalized = (backend or "memory").strip().lower()
    if normalized == "memory":
        from langgraph.checkpoint.memory import MemorySaver

        return MemorySaver()

    if normalized in {"sqlite", "async_sqlite", "sqlite+async"}:
        use_async = async_mode or normalized in {"async_sqlite", "sqlite+async"}
        if not conn_string or not conn_string.strip():
            raise ValueError("conn_string is required for sqlite checkpointer")
        if use_async:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            return AsyncSqliteSaver.from_conn_string(conn_string)

        import sqlite3
        from langgraph.checkpoint.sqlite import SqliteSaver

        conn = sqlite3.connect(conn_string, check_same_thread=False)
        saver = SqliteSaver(conn)
        saver.setup()
        return saver

    if normalized in {"postgres", "postgresql", "async_postgres", "postgres+async"}:
        use_async = async_mode or normalized in {"async_postgres", "postgres+async"}
        if not conn_string or not conn_string.strip():
            raise ValueError("conn_string is required for postgres checkpointer")
        if not (
            conn_string.startswith("postgresql://")
            or conn_string.startswith("postgres://")
            or "host=" in conn_string
            or "dbname=" in conn_string
        ):
            raise ValueError(
                "Invalid postgres conn_string; expected a postgresql:// URI or libpq DSN"
            )
        if use_async:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

            return AsyncPostgresSaver.from_conn_string(conn_string)

        from langgraph.checkpoint.postgres import PostgresSaver
        from psycopg import Connection
        from psycopg.rows import dict_row

        conn = Connection.connect(
            conn_string, autocommit=True, prepare_threshold=0, row_factory=dict_row
        )
        saver = PostgresSaver(conn)
        saver.setup()
        return saver

    raise ValueError(
        f"Unsupported checkpointer backend '{backend}'. Expected 'memory', 'sqlite', or 'postgres'."
    )


def _make_event(
    event_type: EventType,
    agent_id: str,
    task_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return AgentEventPayload(
        event_type=event_type,
        agent_id=agent_id,
        task_id=task_id,
        payload=payload or {},
    ).model_dump(mode="json")


def _estimate_tokens(messages: list[dict[str, Any]]) -> int:
    total_chars = sum(len(str(msg.get("content", ""))) + len(str(msg.get("role", ""))) for msg in messages)
    return max(1, total_chars // 4) if messages else 0


def _compact_sub_agent_state(
    sub_state: dict[str, Any],
    context_window: int,
    threshold: float = 0.75,
    keep_recent: int = 5,
) -> dict[str, Any]:
    """Compact message history when token usage hits >= 75% of context_window."""
    threshold_tokens = int(context_window * threshold)
    messages = list(sub_state.get("messages", []))
    token_count = int(sub_state.get("token_count", 0))
    if threshold_tokens <= 0 or token_count < threshold_tokens or not messages:
        return sub_state

    summary_marker = "[CONTEXT COMPACTION SUMMARY]"
    if len(messages) <= keep_recent and str(messages[0].get("content", "")).startswith(summary_marker):
        return sub_state

    older = messages[:-keep_recent] if len(messages) > keep_recent else []
    recent = messages[-keep_recent:] if len(messages) > keep_recent else messages
    goal_text = sub_state.get("goal", "")
    modified = ", ".join(sub_state.get("modified_files", [])) or "none"
    current_error = sub_state.get("current_error") or "none"
    older_digest = "; ".join(
        f"{m.get('role', 'msg')}: {str(m.get('content', ''))[:100]}"
        for m in older
    )
    summary_content = (
        f"{summary_marker}\n"
        f"Goal: {goal_text}\n"
        f"Modified files: {modified}\n"
        f"Current error: {current_error}\n"
        f"Compacted {len(older)} earlier turns: {older_digest or 'none'}"
    )
    summary_msg = {"role": "system", "content": summary_content}
    compacted_messages = [summary_msg, *recent]
    sub_state["messages"] = compacted_messages
    sub_state["token_count"] = _estimate_tokens(compacted_messages)
    return sub_state


def _find_task_index(
    tasks: list[dict[str, Any]],
    task_id: str,
    agent_id: str | None = None,
) -> int | None:
    for idx, task in enumerate(tasks):
        if task_id and task.get("task_id") == task_id:
            return idx
    if agent_id:
        for idx, task in enumerate(tasks):
            if task.get("assigned_agent") == agent_id and task.get("status") != TaskStatus.DONE.value:
                return idx
    return None


def _default_sub_agent_dump(agent_id: str, goal: str, task_id: str = "") -> dict[str, Any]:
    return SubAgentState(
        agent_id=agent_id,
        task_id=task_id,
        goal=goal,
    ).model_dump(mode="json")


class SquadOrchestrator:
    """Builds and executes a cyclic, domain-agnostic LangGraph workflow from a SquadTemplate."""

    def __init__(
        self,
        squad: SquadTemplate,
        checkpointer: Any | None = None,
        agent_handlers: dict[str, AgentHandler] | None = None,
    ) -> None:
        self.squad = squad
        self.checkpointer = checkpointer if checkpointer is not None else create_checkpointer("memory")
        self.agent_handlers: dict[str, AgentHandler] = dict(agent_handlers or {})

        self._workers: list[AgentSpec] = [m for m in self.squad.members if not m.is_reviewer]
        self._reviewers: list[AgentSpec] = [m for m in self.squad.members if m.is_reviewer]
        if self.squad.supervisor.is_reviewer:
            self._reviewers.append(self.squad.supervisor)
        self._worker_names: set[str] = {w.role_name for w in self._workers}
        self._worker_to_reviewer: dict[str, str] = self._map_workers_to_reviewers()

        self.graph: StateGraph = self._build_graph()
        self._compiled = self.compile()

    def _map_workers_to_reviewers(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for worker in self._workers:
            for reviewer in self._reviewers:
                if worker.role_name in reviewer.reviews:
                    mapping[worker.role_name] = reviewer.role_name
                    break
        return mapping

    def _build_graph(self) -> StateGraph:
        graph = StateGraph(OrchestratorState)
        supervisor_role = self.squad.supervisor.role_name

        graph.add_node(supervisor_role, self._make_supervisor_node())
        graph.add_node("hitl_gate", self._make_hitl_gate_node())

        for worker in self._workers:
            graph.add_node(worker.role_name, self._make_worker_node(worker))

        for reviewer in self._reviewers:
            if reviewer.role_name != supervisor_role:
                graph.add_node(reviewer.role_name, self._make_reviewer_node(reviewer))

        graph.add_edge(START, supervisor_role)

        supervisor_routes: dict[Any, str] = {w.role_name: w.role_name for w in self._workers}
        supervisor_routes[END] = END
        graph.add_conditional_edges(
            supervisor_role,
            lambda s: s["next_agent"] if s.get("next_agent") in self._worker_names else END,
            supervisor_routes,
        )

        for worker in self._workers:
            reviewer_role = self._worker_to_reviewer[worker.role_name]
            worker_routes: dict[Any, str] = {
                "hitl_gate": "hitl_gate",
                reviewer_role: reviewer_role,
            }
            graph.add_conditional_edges(
                worker.role_name,
                lambda s, rev=reviewer_role: "hitl_gate" if s.get("next_agent") == "hitl_gate" else rev,
                worker_routes,
            )

        hitl_routes: dict[Any, str] = {w.role_name: w.role_name for w in self._workers}
        hitl_routes[END] = END
        graph.add_conditional_edges(
            "hitl_gate",
            lambda s: s["next_agent"] if s.get("next_agent") in self._worker_names else END,
            hitl_routes,
        )

        for reviewer in self._reviewers:
            if reviewer.role_name == supervisor_role:
                continue
            reviewer_routes: dict[Any, str] = {
                supervisor_role: supervisor_role,
                END: END,
            }
            for target in reviewer.reviews:
                if target in self._worker_names:
                    reviewer_routes[target] = target
            graph.add_conditional_edges(
                reviewer.role_name,
                lambda s, sup=supervisor_role, allowed=set(reviewer_routes): (
                    s["next_agent"] if s.get("next_agent") in allowed else sup
                ),
                reviewer_routes,
            )

        return graph

    def compile(self) -> Any:
        """Compile the StateGraph with checkpointer and HITL interrupt gate."""
        self._compiled = self.graph.compile(
            checkpointer=self.checkpointer,
            interrupt_before=["hitl_gate"],
        )
        return self._compiled

    def get_graph_topology(self) -> dict[str, Any]:
        """Return nodes, edges, interrupt_before, and mermaid diagram for the compiled graph."""
        compiled = self._compiled or self.compile()
        drawable = compiled.get_graph()
        return {
            "nodes": list(drawable.nodes.keys()),
            "edges": [
                {
                    "source": edge.source,
                    "target": edge.target,
                    "conditional": bool(edge.conditional),
                    "data": edge.data,
                }
                for edge in drawable.edges
            ],
            "interrupt_before": ["hitl_gate"],
            "mermaid": drawable.draw_mermaid(),
        }

    def _resolve_goal(self, state: OrchestratorState) -> str:
        supervisor_role = self.squad.supervisor.role_name
        sub_agents = state.get("sub_agents", {})
        if supervisor_role in sub_agents and sub_agents[supervisor_role].get("goal"):
            return str(sub_agents[supervisor_role]["goal"])
        for sub_state in sub_agents.values():
            if sub_state.get("goal"):
                return str(sub_state["goal"])
        return "Execute squad goal"

    def _synthesize_final_output(self, state: OrchestratorState, tasks: list[dict[str, Any]]) -> str:
        goal = self._resolve_goal(state)
        sub_agents = state.get("sub_agents", {})
        lines: list[str] = []
        for task in tasks:
            agent_id = str(task.get("assigned_agent") or "")
            agent_msgs = sub_agents.get(agent_id, {}).get("messages", [])
            last_msg = next(
                (str(m.get("content", "")) for m in reversed(agent_msgs) if m.get("role") == "assistant"),
                "",
            )
            lines.append(f"- {agent_id} ({task.get('task_id')}): {last_msg or task.get('title')}")
        if not lines:
            return f"Squad '{self.squad.name}' completed goal: {goal}"
        return f"Squad '{self.squad.name}' completed goal: {goal}\n" + "\n".join(lines)

    def _make_supervisor_node(self) -> Callable[[OrchestratorState], dict[str, Any]]:
        supervisor_spec = self.squad.supervisor
        supervisor_role = supervisor_spec.role_name

        def supervisor_node(state: OrchestratorState) -> dict[str, Any]:
            shared = copy.deepcopy(state.get("shared", {}))
            sub_agents = copy.deepcopy(state.get("sub_agents", {}))
            events = list(state.get("events", []))
            final_output = state.get("final_output")
            current_task_id = state.get("current_task_id", "")

            shared["step_count"] = int(shared.get("step_count", 0)) + 1
            step_count = shared["step_count"]
            max_steps = int(shared.get("max_steps", self.squad.max_steps))
            accumulated_cost = float(shared.get("accumulated_cost_usd", 0.0))
            budget_limit = float(shared.get("budget_limit_usd", self.squad.budget_limit_usd))

            if step_count > max_steps or accumulated_cost > budget_limit:
                reason = (
                    f"max_steps exceeded ({step_count} > {max_steps})"
                    if step_count > max_steps
                    else f"budget_limit_usd exceeded ({accumulated_cost:.6f} > {budget_limit:.6f})"
                )
                tasks = list(shared.get("tasks", []))
                for task in tasks:
                    if task.get("status") == TaskStatus.RUNNING.value:
                        task["status"] = TaskStatus.NEEDS_HUMAN_HELP.value
                shared["tasks"] = tasks
                events.append(
                    _make_event(
                        EventType.AGENT_STATE_CHANGE,
                        agent_id=supervisor_role,
                        task_id=current_task_id or "squad",
                        payload={
                            "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                            "reason": reason,
                            "step_count": step_count,
                            "accumulated_cost_usd": accumulated_cost,
                        },
                    )
                )
                return {
                    "shared": shared,
                    "sub_agents": sub_agents,
                    "active_agent": supervisor_role,
                    "next_agent": END,
                    "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                    "events": events,
                    "final_output": final_output,
                }

            handler = self.agent_handlers.get(supervisor_role)
            if handler is not None:
                working_state = copy.deepcopy(state)
                working_state["shared"] = copy.deepcopy(shared)
                working_state["sub_agents"] = copy.deepcopy(sub_agents)
                handler_res = handler(working_state, supervisor_spec) or {}
                if isinstance(handler_res.get("shared"), dict):
                    shared = copy.deepcopy(handler_res["shared"])
                if isinstance(handler_res.get("sub_agents"), dict):
                    sub_agents.update(copy.deepcopy(handler_res["sub_agents"]))
                if handler_res.get("final_output") is not None:
                    final_output = str(handler_res["final_output"])
                if isinstance(handler_res.get("events"), list):
                    events.extend(copy.deepcopy(handler_res["events"]))

            tasks = list(shared.get("tasks", []))
            goal = self._resolve_goal(state)
            if not tasks:
                for idx, worker in enumerate(self._workers, start=1):
                    task_id = f"task-{idx}-{worker.role_name}"
                    title = goal if len(self._workers) == 1 else f"{goal} [{worker.role_name}]"
                    tasks.append(
                        TaskItem(
                            task_id=task_id,
                            title=title,
                            assigned_agent=worker.role_name,
                            status=TaskStatus.PENDING,
                        ).model_dump(mode="json")
                    )
                    if worker.role_name not in sub_agents:
                        sub_agents[worker.role_name] = _default_sub_agent_dump(
                            worker.role_name, goal, task_id=task_id
                        )
                shared["tasks"] = tasks

            next_task: dict[str, Any] | None = None
            for task in tasks:
                if task.get("status") in {TaskStatus.PENDING.value, TaskStatus.RUNNING.value}:
                    next_task = task
                    break

            if next_task is not None:
                next_task["status"] = TaskStatus.RUNNING.value
                target_agent = str(next_task.get("assigned_agent") or "")
                target_task_id = str(next_task.get("task_id") or "")
                if target_agent not in sub_agents:
                    sub_agents[target_agent] = _default_sub_agent_dump(
                        target_agent, goal, task_id=target_task_id
                    )
                else:
                    sub_agents[target_agent]["task_id"] = target_task_id

                shared["tasks"] = tasks
                events.append(
                    _make_event(
                        EventType.AGENT_STATE_CHANGE,
                        agent_id=supervisor_role,
                        task_id=target_task_id,
                        payload={
                            "status": TaskStatus.RUNNING.value,
                            "next_agent": target_agent,
                            "step_count": step_count,
                        },
                    )
                )
                return {
                    "shared": shared,
                    "sub_agents": sub_agents,
                    "active_agent": supervisor_role,
                    "next_agent": target_agent,
                    "current_task_id": target_task_id,
                    "status": TaskStatus.RUNNING.value,
                    "events": events,
                    "final_output": final_output,
                }

            all_done = all(t.get("status") == TaskStatus.DONE.value for t in tasks)
            final_status = TaskStatus.DONE.value if all_done else TaskStatus.NEEDS_HUMAN_HELP.value
            if all_done and not final_output:
                final_output = self._synthesize_final_output(
                    {**state, "sub_agents": sub_agents}, tasks
                )

            events.append(
                _make_event(
                    EventType.AGENT_STATE_CHANGE,
                    agent_id=supervisor_role,
                    task_id=current_task_id or "squad",
                    payload={
                        "status": final_status,
                        "step_count": step_count,
                        "completed_tasks": len(tasks),
                    },
                )
            )
            return {
                "shared": shared,
                "sub_agents": sub_agents,
                "active_agent": supervisor_role,
                "next_agent": END,
                "status": final_status,
                "events": events,
                "final_output": final_output,
            }

        return supervisor_node

    def _make_worker_node(self, member: AgentSpec) -> Callable[[OrchestratorState], dict[str, Any]]:
        role_name = member.role_name
        reviewer_role = self._worker_to_reviewer[role_name]

        def worker_node(state: OrchestratorState) -> dict[str, Any]:
            shared = copy.deepcopy(state.get("shared", {}))
            sub_agents = copy.deepcopy(state.get("sub_agents", {}))
            requested_tools = copy.deepcopy(state.get("requested_tools", {}))
            events = list(state.get("events", []))
            current_task_id = state.get("current_task_id", "")
            goal = self._resolve_goal(state)

            sub_state = copy.deepcopy(
                sub_agents.get(role_name) or _default_sub_agent_dump(role_name, goal, current_task_id)
            )
            sub_state["task_id"] = current_task_id
            sub_state = _compact_sub_agent_state(sub_state, member.llm.context_window)

            agent_tools = list(requested_tools.get(role_name, []))
            sensitive_tools = [t for t in agent_tools if t in member.hitl_tools]
            was_hitl_approved = state.get("hitl_approved") is True

            if sensitive_tools and not was_hitl_approved:
                pending_hitl = {
                    "agent_id": role_name,
                    "task_id": current_task_id,
                    "tool": sensitive_tools[0],
                }
                sub_agents[role_name] = sub_state
                events.append(
                    _make_event(
                        EventType.HITL_INTERRUPT_REQUIRED,
                        agent_id=role_name,
                        task_id=current_task_id,
                        payload={"tool": sensitive_tools[0], "pending_hitl": pending_hitl},
                    )
                )
                return {
                    "shared": shared,
                    "sub_agents": sub_agents,
                    "active_agent": role_name,
                    "next_agent": "hitl_gate",
                    "pending_hitl": pending_hitl,
                    "status": TaskStatus.AWAITING_APPROVAL.value,
                    "events": events,
                }

            tools_to_emit = list(agent_tools)
            prev_cost = float(shared.get("accumulated_cost_usd", 0.0))
            prev_tokens = int(sub_state.get("token_count", 0))
            prev_msg_count = len(sub_state.get("messages", []))
            final_output = state.get("final_output")

            handler = self.agent_handlers.get(role_name)
            if handler is not None:
                working_state = copy.deepcopy(state)
                working_state["shared"] = copy.deepcopy(shared)
                working_state["sub_agents"] = copy.deepcopy(sub_agents)
                working_state["sub_agents"][role_name] = copy.deepcopy(sub_state)
                handler_res = handler(working_state, member) or {}

                if isinstance(handler_res.get("shared"), dict):
                    shared = copy.deepcopy(handler_res["shared"])
                if isinstance(handler_res.get("sub_agents"), dict):
                    for k, v in handler_res["sub_agents"].items():
                        sub_agents[k] = copy.deepcopy(v)
                    if role_name in sub_agents:
                        sub_state = copy.deepcopy(sub_agents[role_name])
                if isinstance(handler_res.get("requested_tools"), dict):
                    requested_tools = copy.deepcopy(handler_res["requested_tools"])
                if handler_res.get("final_output") is not None:
                    final_output = str(handler_res["final_output"])
                if isinstance(handler_res.get("events"), list):
                    events.extend(copy.deepcopy(handler_res["events"]))

                if "modified_files" in handler_res and isinstance(handler_res["modified_files"], list):
                    existing_files = list(sub_state.get("modified_files", []))
                    for path in handler_res["modified_files"]:
                        if path not in existing_files:
                            existing_files.append(path)
                    sub_state["modified_files"] = existing_files

                if "error" in handler_res:
                    sub_state["current_error"] = (
                        str(handler_res["error"]) if handler_res["error"] is not None else None
                    )
                elif "current_error" in handler_res:
                    sub_state["current_error"] = (
                        str(handler_res["current_error"])
                        if handler_res["current_error"] is not None
                        else None
                    )
                elif not (isinstance(handler_res.get("sub_agents"), dict) and role_name in handler_res["sub_agents"]):
                    sub_state["current_error"] = None

                msg_text = (
                    handler_res.get("message")
                    or handler_res.get("output")
                    or handler_res.get("content")
                    or handler_res.get("result")
                )
                if msg_text is not None:
                    sub_state.setdefault("messages", []).append(
                        {"role": "assistant", "content": str(msg_text)}
                    )
                elif len(sub_state.get("messages", [])) == prev_msg_count:
                    sub_state.setdefault("messages", []).append(
                        {
                            "role": "assistant",
                            "content": f"[{role_name}] Completed step for task {current_task_id}",
                        }
                    )

                if not tools_to_emit:
                    if isinstance(handler_res.get("tools"), list):
                        tools_to_emit = [str(t) for t in handler_res["tools"]]
                    elif handler_res.get("tool"):
                        tools_to_emit = [str(handler_res["tool"])]

                if "token_count" in handler_res:
                    sub_state["token_count"] = int(handler_res["token_count"])
                elif "tokens" in handler_res:
                    sub_state["token_count"] = int(sub_state.get("token_count", 0)) + int(handler_res["tokens"])
                elif int(sub_state.get("token_count", 0)) == prev_tokens:
                    added_msgs = sub_state.get("messages", [])[prev_msg_count:]
                    sub_state["token_count"] = prev_tokens + max(25, _estimate_tokens(added_msgs))

                if "cost_usd" in handler_res:
                    shared["accumulated_cost_usd"] = round(
                        float(shared.get("accumulated_cost_usd", 0.0)) + float(handler_res["cost_usd"]),
                        6,
                    )
                elif float(shared.get("accumulated_cost_usd", 0.0)) == prev_cost:
                    shared["accumulated_cost_usd"] = round(prev_cost + 0.001, 6)
            else:
                if not tools_to_emit:
                    tools_to_emit = [member.allowed_mcp_tools[0]] if member.allowed_mcp_tools else ["agent_step"]
                tasks = shared.get("tasks", [])
                task_idx = _find_task_index(tasks, current_task_id, role_name)
                task_title = tasks[task_idx]["title"] if task_idx is not None else goal
                msg_content = (
                    f"[{role_name}] Executed {', '.join(tools_to_emit)} for task: {task_title}"
                )
                sub_state.setdefault("messages", []).append({"role": "assistant", "content": msg_content})
                sub_state["current_error"] = None
                step_tokens = max(50, len(msg_content) // 4 + 25)
                sub_state["token_count"] = prev_tokens + step_tokens
                shared["accumulated_cost_usd"] = round(prev_cost + 0.001, 6)

            if not tools_to_emit:
                tools_to_emit = [member.allowed_mcp_tools[0]] if member.allowed_mcp_tools else ["agent_step"]

            sub_state = _compact_sub_agent_state(sub_state, member.llm.context_window)
            sub_agents[role_name] = sub_state

            for tool_name in tools_to_emit:
                events.append(
                    _make_event(
                        EventType.TOOL_CALL_START,
                        agent_id=role_name,
                        task_id=current_task_id,
                        payload={"tool": tool_name, "hitl_approved": was_hitl_approved},
                    )
                )
                events.append(
                    _make_event(
                        EventType.TOOL_CALL_RESULT,
                        agent_id=role_name,
                        task_id=current_task_id,
                        payload={
                            "tool": tool_name,
                            "ok": sub_state.get("current_error") is None,
                            "error": sub_state.get("current_error"),
                        },
                    )
                )

            return {
                "shared": shared,
                "sub_agents": sub_agents,
                "active_agent": role_name,
                "next_agent": reviewer_role,
                "pending_hitl": None,
                "hitl_approved": None,
                "requested_tools": requested_tools,
                "status": TaskStatus.RUNNING.value,
                "events": events,
                "final_output": final_output,
            }

        return worker_node

    def _make_hitl_gate_node(self) -> Callable[[OrchestratorState], dict[str, Any]]:
        def hitl_gate_node(state: OrchestratorState) -> dict[str, Any]:
            shared = copy.deepcopy(state.get("shared", {}))
            events = list(state.get("events", []))
            current_task_id = state.get("current_task_id", "")
            waiting_agent = state.get("active_agent", "")
            approved = state.get("hitl_approved") is True

            if approved:
                events.append(
                    _make_event(
                        EventType.AGENT_STATE_CHANGE,
                        agent_id="hitl_gate",
                        task_id=current_task_id,
                        payload={
                            "status": TaskStatus.RUNNING.value,
                            "approved": True,
                            "resume_agent": waiting_agent,
                        },
                    )
                )
                return {
                    "pending_hitl": None,
                    "next_agent": waiting_agent,
                    "status": TaskStatus.RUNNING.value,
                    "events": events,
                }

            tasks = list(shared.get("tasks", []))
            task_idx = _find_task_index(tasks, current_task_id, waiting_agent)
            if task_idx is not None:
                tasks[task_idx]["status"] = TaskStatus.NEEDS_HUMAN_HELP.value
                shared["tasks"] = tasks

            events.append(
                _make_event(
                    EventType.AGENT_STATE_CHANGE,
                    agent_id="hitl_gate",
                    task_id=current_task_id,
                    payload={
                        "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                        "approved": False,
                        "rejected_agent": waiting_agent,
                    },
                )
            )
            return {
                "shared": shared,
                "pending_hitl": None,
                "hitl_approved": None,
                "next_agent": END,
                "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                "events": events,
            }

        return hitl_gate_node

    def _parse_review_decision(
        self,
        handler_res: dict[str, Any],
        worker_role: str,
        worker_sub_state: dict[str, Any],
    ) -> tuple[bool, str]:
        if isinstance(handler_res.get("review_decisions"), dict) and worker_role in handler_res["review_decisions"]:
            entry = handler_res["review_decisions"][worker_role]
            if isinstance(entry, dict) and "approved" in entry:
                return bool(entry["approved"]), str(entry.get("feedback") or entry.get("reason") or "Rejected by reviewer")

        if "approved" in handler_res:
            approved = bool(handler_res["approved"])
            reason = str(handler_res.get("feedback") or handler_res.get("reason") or ("Approved" if approved else "Rejected by reviewer"))
            return approved, reason

        decision = str(handler_res.get("decision") or handler_res.get("status") or "").strip().lower()
        if decision in {"approve", "approved", "pass", "ok"}:
            return True, str(handler_res.get("feedback") or "Approved")
        if decision in {"reject", "rejected", "fail", "changes_requested"}:
            return False, str(handler_res.get("feedback") or handler_res.get("reason") or "Rejected by reviewer")

        if worker_sub_state.get("current_error"):
            return False, str(worker_sub_state["current_error"])
        return True, str(handler_res.get("feedback") or "Approved")

    def _make_reviewer_node(self, reviewer: AgentSpec) -> Callable[[OrchestratorState], dict[str, Any]]:
        reviewer_role = reviewer.role_name
        supervisor_role = self.squad.supervisor.role_name

        def reviewer_node(state: OrchestratorState) -> dict[str, Any]:
            shared = copy.deepcopy(state.get("shared", {}))
            sub_agents = copy.deepcopy(state.get("sub_agents", {}))
            review_counts = dict(state.get("review_counts", {}))
            review_decisions = copy.deepcopy(state.get("review_decisions", {}))
            events = list(state.get("events", []))
            current_task_id = state.get("current_task_id", "")
            tasks = list(shared.get("tasks", []))

            worker_role = state.get("active_agent", "")
            if worker_role not in reviewer.reviews:
                task_idx = _find_task_index(tasks, current_task_id)
                if task_idx is not None and tasks[task_idx].get("assigned_agent") in reviewer.reviews:
                    worker_role = str(tasks[task_idx]["assigned_agent"])
                elif reviewer.reviews:
                    worker_role = reviewer.reviews[0]

            goal = self._resolve_goal(state)
            worker_sub_dict = copy.deepcopy(
                sub_agents.get(worker_role) or _default_sub_agent_dump(worker_role, goal, current_task_id)
            )

            handler = self.agent_handlers.get(reviewer_role)
            handler_res: dict[str, Any] = {}
            if handler is not None:
                working_state = copy.deepcopy(state)
                working_state["shared"] = copy.deepcopy(shared)
                working_state["sub_agents"] = copy.deepcopy(sub_agents)
                handler_res = handler(working_state, reviewer) or {}
                if isinstance(handler_res.get("shared"), dict):
                    shared = copy.deepcopy(handler_res["shared"])
                    tasks = list(shared.get("tasks", []))
                if isinstance(handler_res.get("sub_agents"), dict):
                    for k, v in handler_res["sub_agents"].items():
                        sub_agents[k] = copy.deepcopy(v)
                    if worker_role in sub_agents:
                        worker_sub_dict = copy.deepcopy(sub_agents[worker_role])
                if "cost_usd" in handler_res:
                    shared["accumulated_cost_usd"] = round(
                        float(shared.get("accumulated_cost_usd", 0.0)) + float(handler_res["cost_usd"]),
                        6,
                    )
                if isinstance(handler_res.get("events"), list):
                    events.extend(copy.deepcopy(handler_res["events"]))

            approved, feedback = self._parse_review_decision(handler_res, worker_role, worker_sub_dict)

            if approved:
                task_idx = _find_task_index(tasks, current_task_id, worker_role)
                if task_idx is not None:
                    tasks[task_idx]["status"] = TaskStatus.DONE.value
                    shared["tasks"] = tasks

                worker_sub_dict["current_error"] = None
                worker_sub_dict["error_signature_attempts"] = {}
                sub_agents[worker_role] = worker_sub_dict

                review_decisions[worker_role] = {
                    "approved": True,
                    "reviewer": reviewer_role,
                    "feedback": feedback,
                    "rejections": review_counts.get(worker_role, 0),
                }
                events.append(
                    _make_event(
                        EventType.AGENT_STATE_CHANGE,
                        agent_id=reviewer_role,
                        task_id=current_task_id,
                        payload={
                            "status": TaskStatus.DONE.value,
                            "reviewed_agent": worker_role,
                            "approved": True,
                            "feedback": feedback,
                        },
                    )
                )
                return {
                    "shared": shared,
                    "sub_agents": sub_agents,
                    "review_counts": review_counts,
                    "review_decisions": review_decisions,
                    "active_agent": reviewer_role,
                    "next_agent": supervisor_role,
                    "status": TaskStatus.RUNNING.value,
                    "events": events,
                }

            new_count = review_counts.get(worker_role, 0) + 1
            review_counts[worker_role] = new_count

            sub_model = SubAgentState.model_validate(worker_sub_dict)
            same_error_exceeded = sub_model.record_error(feedback)
            updated_worker_dict = sub_model.model_dump(mode="json")
            updated_worker_dict.setdefault("messages", []).append(
                {
                    "role": "user",
                    "content": f"Review feedback from {reviewer_role} (attempt {new_count}): {feedback}",
                }
            )
            sub_agents[worker_role] = updated_worker_dict

            review_decisions[worker_role] = {
                "approved": False,
                "reviewer": reviewer_role,
                "feedback": feedback,
                "rejections": new_count,
            }

            if new_count >= self.squad.max_review_rejections or same_error_exceeded:
                task_idx = _find_task_index(tasks, current_task_id, worker_role)
                if task_idx is not None:
                    tasks[task_idx]["status"] = TaskStatus.NEEDS_HUMAN_HELP.value
                    shared["tasks"] = tasks

                events.append(
                    _make_event(
                        EventType.AGENT_STATE_CHANGE,
                        agent_id=reviewer_role,
                        task_id=current_task_id,
                        payload={
                            "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                            "reviewed_agent": worker_role,
                            "approved": False,
                            "feedback": feedback,
                            "rejections": new_count,
                        },
                    )
                )
                return {
                    "shared": shared,
                    "sub_agents": sub_agents,
                    "review_counts": review_counts,
                    "review_decisions": review_decisions,
                    "active_agent": reviewer_role,
                    "next_agent": END,
                    "status": TaskStatus.NEEDS_HUMAN_HELP.value,
                    "events": events,
                }

            events.append(
                _make_event(
                    EventType.AGENT_STATE_CHANGE,
                    agent_id=reviewer_role,
                    task_id=current_task_id,
                    payload={
                        "status": TaskStatus.RUNNING.value,
                        "reviewed_agent": worker_role,
                        "approved": False,
                        "feedback": feedback,
                        "rejections": new_count,
                    },
                )
            )
            return {
                "shared": shared,
                "sub_agents": sub_agents,
                "review_counts": review_counts,
                "review_decisions": review_decisions,
                "active_agent": reviewer_role,
                "next_agent": worker_role,
                "status": TaskStatus.RUNNING.value,
                "events": events,
            }

        return reviewer_node

    def _build_initial_state(
        self,
        goal: str,
        workspace_root: str = ".",
        requested_tools: dict[str, list[str]] | None = None,
    ) -> OrchestratorState:
        shared = SharedWorkspaceState(
            workspace_root=workspace_root,
            budget_limit_usd=self.squad.budget_limit_usd,
            max_steps=self.squad.max_steps,
        ).model_dump(mode="json")

        sub_agents: dict[str, dict[str, Any]] = {}
        for agent in [self.squad.supervisor, *self.squad.members]:
            sub_agents[agent.role_name] = _default_sub_agent_dump(agent.role_name, goal)

        return {
            "shared": shared,
            "sub_agents": sub_agents,
            "active_agent": self.squad.supervisor.role_name,
            "next_agent": self.squad.supervisor.role_name,
            "current_task_id": "",
            "review_counts": {},
            "review_decisions": {},
            "pending_hitl": None,
            "hitl_approved": None,
            "requested_tools": copy.deepcopy(requested_tools or {}),
            "events": [],
            "status": TaskStatus.PENDING.value,
            "final_output": None,
        }

    def _thread_config(self, thread_id: str) -> dict[str, Any]:
        return {
            "configurable": {"thread_id": thread_id},
            "recursion_limit": max(25, self.squad.max_steps * 4 + 20),
        }

    def _build_resume_updates(
        self,
        values: dict[str, Any],
        approved: bool,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        updates: dict[str, Any] = {"hitl_approved": bool(approved)}
        if feedback:
            active_agent = str(values.get("active_agent", ""))
            sub_agents = copy.deepcopy(values.get("sub_agents", {}))
            if active_agent and active_agent in sub_agents:
                sub_agents[active_agent].setdefault("messages", []).append(
                    {
                        "role": "user",
                        "content": f"HITL {'approved' if approved else 'rejected'}: {feedback}",
                    }
                )
                updates["sub_agents"] = sub_agents
        return updates

    def invoke(
        self,
        goal: str,
        workspace_root: str = ".",
        thread_id: str = "default",
        requested_tools: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Run the squad graph synchronously until completion or HITL interrupt."""
        compiled = self._compiled or self.compile()
        initial_state = self._build_initial_state(
            goal=goal,
            workspace_root=workspace_root,
            requested_tools=requested_tools,
        )
        result = compiled.invoke(initial_state, self._thread_config(thread_id))
        return dict(result)

    def get_state(self, thread_id: str) -> dict[str, Any]:
        """Inspect persisted checkpoint state and interrupt status for thread_id."""
        compiled = self._compiled or self.compile()
        snap = compiled.get_state(self._thread_config(thread_id))
        return {
            "values": dict(snap.values) if snap.values else {},
            "next": list(snap.next),
            "is_interrupted": bool(snap.next),
        }

    def resume(
        self,
        thread_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        """Resume an interrupted run after human-in-the-loop approval or rejection."""
        compiled = self._compiled or self.compile()
        config = self._thread_config(thread_id)
        snap = compiled.get_state(config)
        if not snap.values or not snap.next:
            raise ValueError(f"Thread '{thread_id}' is not interrupted or has no active state")

        updates = self._build_resume_updates(dict(snap.values), approved=approved, feedback=feedback)
        compiled.update_state(config, updates)
        result = compiled.invoke(None, config)
        return dict(result)

    async def ainvoke(
        self,
        goal: str,
        workspace_root: str = ".",
        thread_id: str = "default",
        requested_tools: dict[str, list[str]] | None = None,
    ) -> dict[str, Any]:
        """Run the squad graph asynchronously until completion or HITL interrupt."""
        compiled = self._compiled or self.compile()
        initial_state = self._build_initial_state(
            goal=goal,
            workspace_root=workspace_root,
            requested_tools=requested_tools,
        )
        result = await compiled.ainvoke(initial_state, self._thread_config(thread_id))
        return dict(result)

    async def aget_state(self, thread_id: str) -> dict[str, Any]:
        """Asynchronously inspect persisted checkpoint state and interrupt status for thread_id."""
        compiled = self._compiled or self.compile()
        snap = await compiled.aget_state(self._thread_config(thread_id))
        return {
            "values": dict(snap.values) if snap.values else {},
            "next": list(snap.next),
            "is_interrupted": bool(snap.next),
        }

    async def aresume(
        self,
        thread_id: str,
        approved: bool,
        feedback: str | None = None,
    ) -> dict[str, Any]:
        """Asynchronously resume an interrupted run after HITL approval or rejection."""
        compiled = self._compiled or self.compile()
        config = self._thread_config(thread_id)
        snap = await compiled.aget_state(config)
        if not snap.values or not snap.next:
            raise ValueError(f"Thread '{thread_id}' is not interrupted or has no active state")

        updates = self._build_resume_updates(dict(snap.values), approved=approved, feedback=feedback)
        await compiled.aupdate_state(config, updates)
        result = await compiled.ainvoke(None, config)
        return dict(result)
