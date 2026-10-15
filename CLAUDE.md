# CLAUDE.md — Universal Multi-Agent IDE & Continuous Orchestration Platform (Domain-Agnostic)

## 1. Vai Trò Của Claude & Tầm Nhìn Dự Án (Role & Project Vision)

Bạn đóng vai trò là **Principal AI Systems Architect & Lead Agentic Engineer** đồng hành phát triển dự án **Universal Multi-Agent IDE** — một nền tảng điều phối, giám sát và thực thi các đội ngũ AI Agent làm việc liên tục trên thư mục máy tính cục bộ (Local Workspace) **áp dụng cho đa ngành nghề** (Nghiên cứu thị trường, Phân tích dữ liệu & Tài chính, Sáng tạo nội dung & Marketing, Pháp lý & Kiểm toán hồ sơ, và Phát triển phần mềm).

**Mục tiêu cốt lõi của hệ thống:**
- Cho phép người dùng khởi tạo bất kỳ "Đội ngũ Agent chuyên trách" (Agent Squad/Persona) nào thông qua cấu hình động (Dynamic Role Templates) mà không bị bó hẹp vào bài toán viết code.
- Cấp quyền cho các Agent đọc, phân tích, tổng hợp và chỉnh sửa đa định dạng tài liệu trên máy tính (PDF, Word/DOCX, Excel/CSV, Markdown, Báo cáo, Mã nguồn) thông qua bộ công cụ gắn ngoài chuẩn hóa ([Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro) và [Docling](https://github.com/docling-project/docling)).
- Đảm bảo an toàn tuyệt đối cho thư mục gốc của người dùng thông qua cơ chế cô lập không gian làm việc theo phiên/nhánh ([Git Worktree](https://git-scm.com/docs/git-worktree) hoặc Snapshot Sandbox) và môi trường thực thi cô lập ([Docker Bind Mounts](https://docs.docker.com/engine/storage/bind-mounts/)).
- Cung cấp giao diện trực quan hóa luồng suy nghĩ (State Graph), so sánh sự thay đổi của tài liệu/dữ liệu/code (Multi-format Diff Review), kiểm soát chi phí token và cho phép con người phê duyệt (Human-in-the-Loop) trước các thao tác quan trọng.
- Tối ưu hóa tỷ lệ công sức theo chuẩn **AI Engineering (80% Backend Orchestration / Runtime — 20% Frontend UI)**.

## 2. Kiến Trúc Kỹ Thuật & Công Nghệ Cốt Lõi (Tech Stack)

Khi thiết kế tính năng, viết code hoặc đề xuất giải pháp, luôn tuân thủ bộ công nghệ chuẩn dưới đây:

| Phân hệ (Layer) | Công nghệ & Thư viện chuẩn | Trách nhiệm kỹ thuật chính (Đa ngành) |
| :--- | :--- | :--- |
| **Agent Orchestration** | Python 3.11+, [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview), Pydantic v2 | Xây dựng đồ thị trạng thái có chu trình (Cyclic State Graph), định tuyến Supervisor/Sub-agents động theo từng ngành, lưu Checkpoint và xử lý Interrupt (Pause/Resume). |
| **Durable Execution** | [Temporal Python SDK](https://docs.temporal.io/develop/python/), Redis, PostgreSQL | Quản lý các tiến trình agent chạy ngầm dài hạn (hàng giờ/hàng ngày), tự động thử lại (retry) khi lỗi mạng/API và khôi phục trạng thái khi khởi động lại máy. |
| **Multi-Domain Tooling** | [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro), [MCP Filesystem Server](https://github.com/modelcontextprotocol/servers/blob/main/src/filesystem/README.md), [Playwright MCP](https://github.com/microsoft/playwright-mcp), [Docling](https://github.com/docling-project/docling) | Đọc/bóc tách tài liệu phức tạp (PDF, DOCX, XLSX), tự động hóa trình duyệt thu thập dữ liệu web, chỉnh sửa tài liệu/code theo khối (surgical diff) và cô lập nhánh làm việc bằng [Git Worktree](https://git-scm.com/docs/git-worktree). |
| **Sandbox & Isolation** | [Docker SDK for Python](https://docs.docker.com/guides/python/), [Docker Bind Mounts](https://docs.docker.com/engine/storage/bind-mounts/), [E2B Sandbox](https://docs.e2b.dev/quickstart) | Chạy script phân tích dữ liệu (Pandas/Matplotlib), xuất báo cáo hoặc chạy kiểm thử trong container cô lập thay vì chạy trực tiếp trên hệ điều hành chủ. |
| **Memory & Context** | [Mem0](https://github.com/mem0ai/mem0), [Qdrant](https://qdrant.tech/documentation/overview/) | Quản lý bộ nhớ chung (Shared Blackboard) giữa các agent, lưu trữ tri thức chuyên ngành (Domain Knowledge Base) và tự động nén ngữ cảnh (Context Compaction). |
| **Observability & Evals** | [Langfuse](https://langfuse.com/docs/observability/overview) | Ghi vết toàn bộ chuỗi suy luận (Traces/Spans), đo độ trễ, đếm token theo từng agent và ngắt mạch khi chạm trần ngân sách (Budget Circuit Breaker). |
| **Backend API & Sync** | [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/), [Python Watchdog](https://python-watchdog.readthedocs.io/en/stable/quickstart.html) | Truyền luồng sự kiện trạng thái agent, tiến độ công việc và đồng bộ biến động file tài liệu/dữ liệu từ ổ cứng lên giao diện theo thời gian thực. |
| **Frontend IDE UI** | Next.js (App Router), TypeScript, Tailwind CSS, [React Flow](https://reactflow.dev/learn), [Monaco Editor](https://www.npmjs.com/package/@monaco-editor/react), [Xterm.js](https://github.com/xtermjs/xterm.js/blob/master/README.md) | Hiển thị sơ đồ node của đội ngũ agent, trình duyệt cây thư mục, cửa sổ xem trước đa định dạng (Markdown/Bảng biểu/PDF/Code Diff) và màn hình log thực thi trực tiếp. |

## 3. Cấu Trúc Thư Mục Dự Án (Monorepo Structure)

Toàn bộ mã nguồn dự án được tổ chức theo cấu trúc chuẩn sau:

- `backend/` — Dịch vụ Python FastAPI và lõi điều phối Agent đa ngành
  - `backend/app/api/` — REST endpoints và cổng kết nối [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/)
  - `backend/app/agents/` — Hệ thống nạp cấu hình Agent động (`AgentProfile`, `SquadTemplate`) cho từng lĩnh vực (Research, Data/Finance, Content, Legal, Engineering)
  - `backend/app/graphs/` — Định nghĩa State, Nodes, Conditional Edges và Checkpointer của [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)
  - `backend/app/tools/` — Bộ công cụ đa năng: đọc/sửa văn bản & code theo khối, bóc tách PDF/Excel qua [Docling](https://github.com/docling-project/docling), điều khiển trình duyệt qua [Playwright MCP](https://github.com/microsoft/playwright-mcp), và cô lập thư mục
  - `backend/app/sandbox/` — Trình quản lý vòng đời container qua [Docker SDK for Python](https://docs.docker.com/guides/python/) và bộ lắng nghe file [Python Watchdog](https://python-watchdog.readthedocs.io/en/stable/quickstart.html)
  - `backend/app/memory/` — Tích hợp [Mem0](https://github.com/mem0ai/mem0), [Qdrant](https://qdrant.tech/documentation/overview/) và bộ nén ngữ cảnh (Context Summarizer)
  - `backend/app/observability/` — Middleware theo dõi chi phí, giới hạn vòng lặp và tích hợp [Langfuse](https://langfuse.com/docs/observability/overview)
- `frontend/` — Giao diện điều khiển Universal Workspace IDE (Next.js + TypeScript)
  - `frontend/src/components/canvas/` — Sơ đồ luồng đa tác tử xây dựng bằng [React Flow](https://reactflow.dev/learn)
  - `frontend/src/components/workspace/` — Trình xem trước đa định dạng (Document/Spreadsheet/PDF Viewer) và duyệt thay đổi bằng [Monaco Editor](https://www.npmjs.com/package/@monaco-editor/react)
  - `frontend/src/components/terminal/` — Màn hình theo dõi log thực thi và dòng lệnh thời gian thực với [Xterm.js](https://github.com/xtermjs/xterm.js/blob/master/README.md)
- `templates/` — Thư viện mẫu cấu hình đội ngũ Agent (YAML/JSON) cho từng ngành nghề
- `docker/` — Dockerfile cho môi trường Agent Sandbox và `docker-compose.yml` (PostgreSQL, Redis, Qdrant, Langfuse)
- `tests/` — Unit tests và Integration tests cho từng tool, luồng đồ thị và cơ chế khóa thư mục

## 4. Nguyên Tắc Thiết Kế Hệ Thống Đa Tác Tử Đa Ngành (Core Engineering Rules)

Khi viết code cho hệ thống này, Claude **bắt buộc** phải tuân thủ 5 nguyên tắc kỹ thuật sau:

### 4.1. Kiến trúc Không Phụ Thuộc Ngành Nghề (Domain-Agnostic & Pluggable Architecture)
- **Không đóng cứng vai trò Agent (Zero Hardcoded Roles):** Không viết cố định các node chỉ dành cho lập trình viên. Mọi Agent Node trong [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) phải được khởi tạo động từ cấu hình `AgentSpec` (gồm: `role_name`, `system_prompt`, `allowed_mcp_tools`, `output_schema`, `model_config`).
- **Gắn kết công cụ theo mô-đun (Pluggable Toolkits):** Sử dụng [Model Context Protocol (MCP)](https://modelcontextprotocol.io/docs/2026-07-28/getting-started/intro) làm chuẩn giao tiếp duy nhất để dễ dàng hoán đổi bộ công cụ theo ngành (ví dụ: gắn [Docling](https://github.com/docling-project/docling) cho đội Pháp lý/Tài chính đọc PDF/Excel; gắn [Playwright MCP](https://github.com/microsoft/playwright-mcp) cho đội Nghiên cứu thị trường/Marketing thu thập thông tin web).

### 4.2. Tuyệt đối cô lập thư mục và chống xung đột tài liệu (Workspace Isolation)
- **Khóa phạm vi đường dẫn (Path Sandboxing):** Mọi hàm thao tác file phải kiểm tra `Path(target_path).resolve().is_relative_to(workspace_root.resolve())`. Nghiêm cấm cho phép agent truy cập đường dẫn chứa `..` thoát ra ngoài thư mục làm việc đã cấp phép.
- **Cô lập bản nháp khi chạy song song (Draft Worktree / Snapshot Isolation):** Khi nhiều Agent cùng chỉnh sửa báo cáo, bảng dữ liệu hoặc mã nguồn, hệ thống tự động tạo không gian nháp riêng (sử dụng [Git Worktree](https://git-scm.com/docs/git-worktree) cho dự án có Git hoặc thư mục `.agent-drafts/<agent_id>-<task_id>` cho thư mục tài liệu văn phòng). Chỉ khi Reviewer Agent kiểm duyệt đạt yêu cầu và người dùng bấm Approve thì bản nháp mới được cập nhật vào thư mục chính.
- **Chỉnh sửa theo từng phần (Surgical Section/Diff Editing):** Đối với văn bản dài (Markdown, tài liệu, mã nguồn), ưu tiên công cụ chỉnh sửa theo từng mục hoặc khối `SEARCH/REPLACE` thay vì ghi đè toàn bộ file để tiết kiệm token và tránh làm mất nội dung gốc.

### 4.3. Kiểm soát vòng lặp, chi phí và thực thi bền vững (Continuous & Safe Loop)
- **Chống lặp vô hạn (Loop & Cost Guardrails):** Mọi đồ thị [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview) phải cấu hình `recursion_limit` rõ ràng và theo dõi biến `step_count` cũng như `accumulated_cost_usd` trong `AgentState`. Nếu một agent xử lý cùng một lỗi hoặc bị Reviewer trả lại quá 3 lần liên tiếp, tự động chuyển trạng thái sang `NEEDS_HUMAN_HELP` và phát cảnh báo lên UI.
- **Điểm dừng phê duyệt (Human-in-the-Loop Interrupts):** Bắt buộc gắn `interrupt_before` tại các nút thực thi hành động quan trọng hoặc không thể đảo ngược (ví dụ: xóa file tài liệu, ghi đè báo cáo gốc, gửi email/đẩy dữ liệu ra ngoài, chạy lệnh hệ thống).
- **Lưu trạng thái bền vững (Checkpointing):** Luôn sử dụng `AsyncPostgresSaver` hoặc `AsyncSqliteSaver` của LangGraph để mọi quy trình làm việc kéo dài nhiều giờ đều có thể tạm dừng (Pause), tua lại (Time-travel rewind) hoặc tiếp tục (Resume) bất kỳ lúc nào.

### 4.4. Quản lý ngữ cảnh thông minh (Context Engineering)
- **Tách biệt bảng tin chung và bộ nhớ riêng:** `SharedWorkspaceState` chỉ lưu danh sách mục tiêu, tiến độ công việc, danh mục tài liệu trong thư mục và các kết luận đã được xác thực. Lịch sử tra cứu thô, nội dung trang web dài hoặc log chi tiết của từng agent phải nằm trong `SubAgentState` riêng biệt.
- **Tự động tóm tắt lịch sử (Context Compaction):** Khi tổng số token trong phiên làm việc của một agent chạm ngưỡng 75% giới hạn cửa sổ ngữ cảnh, kích hoạt hàm tóm tắt (giữ nguyên mục tiêu gốc, các tài liệu/số liệu đã trích xuất, vấn đề hiện tại và 5 lượt tương tác gần nhất).

### 4.5. Chuẩn hóa giao tiếp thời gian thực & Quy chuẩn viết mã (Streaming & Coding Standards)
- **Sự kiện thời gian thực thống nhất:** Mọi sự kiện truyền từ Backend lên Frontend qua [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/) phải tuân theo `AgentEventPayload` (`AGENT_STATE_CHANGE`, `TOOL_CALL_START`, `TOOL_CALL_RESULT`, `TERMINAL_OUTPUT`, `ARTIFACT_UPDATED`, `DIFF_PROPOSED`, `HITL_INTERRUPT_REQUIRED`, `TOKEN_USAGE_UPDATE`, `RESOURCE_USAGE_UPDATE`).
- **Xử lý lỗi tự phục hồi (Self-Healing Tool Errors):** Khi một công cụ gặp lỗi (ví dụ: file Excel sai tên cột, PDF lỗi định dạng, trang web timeout), bắt lỗi (`try/except`) và trả về thông báo hướng dẫn cụ thể dưới dạng `ToolMessage` để Agent tự điều chỉnh cách gọi ở bước kế tiếp mà không làm sập tiến trình Python.

## 5. Lệnh Phát Triển Thường Dùng (Development Commands)

Sử dụng các lệnh chuẩn dưới đây khi làm việc với kho mã nguồn:

- **Khởi chạy hạ tầng phụ trợ (Postgres, Redis, Qdrant, Langfuse):**
  `docker compose -f docker/docker-compose.yml up -d`
- **Chạy Backend FastAPI & Agent Runtime (chế độ phát triển):**
  `uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000`
- **Chạy kiểm thử đơn vị (Unit & Integration Tests):**
  `pytest tests/ -v --asyncio-mode=auto`
- **Khởi chạy giao diện Frontend IDE:**
  `npm --prefix frontend run dev`