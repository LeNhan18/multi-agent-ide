# CLAUDE.md — Multi-Agent IDE & Continuous Orchestration Platform

## 1. Vai trò & mục tiêu
Claude đóng vai Principal AI Systems Architect & Lead Agentic Engineer cho Multi-Agent IDE: nền tảng điều phối, giám sát và thực thi nhiều AI Agent (Planner, Coder, Reviewer, Researcher) làm việc liên tục trên Local Workspace.

- Điều phối agent chuyên biệt chạy song song, dài hạn.
- An toàn cho thư mục gốc qua Git Worktree + Docker Bind Mounts.
- UI trực quan hóa State Graph, log realtime, chi phí token, Human-in-the-Loop.
- Tỷ lệ công sức: 80% Backend Orchestration / Runtime — 20% Frontend.

## 2. Tech stack
| Layer | Công nghệ | Trách nhiệm |
|---|---|---|
| Agent Orchestration | Python 3.11+, LangGraph, Pydantic v2 | Cyclic State Graph, Supervisor/Sub-agents, Checkpoint, Interrupt |
| Durable Execution | Temporal, Redis, PostgreSQL | Tiến trình dài hạn, retry, khôi phục |
| Workspace & Tooling | MCP, MCP Filesystem Server, Git Worktree | Surgical diff, tìm kiếm, worktree riêng mỗi agent |
| Sandbox | Docker SDK, Bind Mounts, E2B | Chạy bash/test/cài gói trong container |
| Memory | Mem0, Qdrant | Shared Blackboard, lịch sử quyết định, Context Compaction |
| Observability | Langfuse | Traces, token theo agent, Budget Circuit Breaker |
| Backend API | FastAPI WebSockets, Watchdog | Stream sự kiện, log, đồng bộ file |
| Frontend | Next.js, TypeScript, Tailwind, React Flow, Monaco, Xterm.js | Canvas agent, cây thư mục, Diff Approve/Reject, terminal |

## 3. Cấu trúc monorepo
- `backend/app/api/` — REST + WebSocket
- `backend/app/agents/` — định nghĩa agent & system prompt
- `backend/app/graphs/` — State, Nodes, Edges, Checkpointer
- `backend/app/tools/` — read_file_lines, grep_search, apply_diff, worktree, MCP
- `backend/app/sandbox/` — Docker lifecycle, Watchdog
- `backend/app/memory/` — Mem0, Qdrant, Context Summarizer
- `backend/app/observability/` — cost, loop limit, Langfuse
- `frontend/src/components/{canvas,editor,terminal}/`
- `docker/` — Sandbox Dockerfile, docker-compose.yml
- `tests/`

## 4. Nguyên tắc kỹ thuật
### 4.1 Workspace Isolation
- Path sandboxing: `Path(target).resolve().is_relative_to(workspace_root.resolve())`.
- Song song: `git worktree add .agent-worktrees/<agent_id>-<task_id> -b agent/<agent_id>/<task_id>`; merge chỉ khi Reviewer pass test và người dùng Approve.
- File > 100 dòng: dùng `apply_search_replace_diff`, không overwrite.

### 4.2 Loop, chi phí, bền vững
- `recursion_limit`, `step_count`, `accumulated_cost_usd` trong state; cùng lỗi 3 lần → `NEEDS_HUMAN_HELP`.
- `interrupt_before` cho xóa file, migration, git push, cài gói hệ thống.
- Checkpoint bằng `AsyncPostgresSaver` / `AsyncSqliteSaver`, không lưu state trong RAM.

### 4.3 Context Engineering
- `SharedWorkspaceState`: task, trạng thái chung, cây thư mục rút gọn, quyết định kiến trúc.
- `SubAgentState`: lịch sử tool và log riêng từng agent.
- 75% context → tóm tắt (giữ mục tiêu, file đã sửa, lỗi hiện tại, 5 lượt gần nhất).

### 4.4 Event Streaming
`AgentEventPayload`: `event_type` (AGENT_STATE_CHANGE | TOOL_CALL_START | TOOL_CALL_RESULT | TERMINAL_OUTPUT | FILE_DIFF_PROPOSED | HITL_INTERRUPT_REQUIRED | TOKEN_USAGE_UPDATE | RESOURCE_USAGE_UPDATE), `agent_id`, `task_id`, `timestamp`, `payload`.

### 4.5 Coding guidelines
- Async toàn bộ, typing + Pydantic cho State, ToolSchema, structured output.
- Tool lỗi → try/except, trả ToolMessage có hướng dẫn khắc phục.
- Khi làm tính năng: (1) AgentState, (2) Tool/Node, (3) luồng WebSocket, rồi code hoàn chỉnh, không TODO/pass.

## 5. Lệnh phát triển
```bash
docker compose -f docker/docker-compose.yml up -d
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
pytest tests/ -v --asyncio-mode=auto
npm --prefix frontend run dev
```
