# Multi-Agent IDE

Domain-agnostic platform for orchestrating long-running AI agent squads (research, data/finance, content, legal, engineering) on a local workspace, with draft isolation, Docker sandboxing, human-in-the-loop approval and cost guardrails. Squads are defined in `templates/*.yaml`.

See `CLAUDE.md` for architecture and engineering rules.

## Dev

```bash
docker compose -f docker/docker-compose.yml up -d
pip install -e ".[dev]"
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
pytest
```

## AI Office Workflow Lab

Start the loopback-only demo server with Node.js 22 or later:

```bash
node frontend/workflow-lab-server/local.mjs
```

Open <http://127.0.0.1:8080/workflow-lab/>. The task board starts empty and reads previously saved browser data. The 3D office runs simulated handoffs between six agents. Agent policies define skills, constraints, and allowed demo events.

The short task form works without credentials. To enable AI planning, provide `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `GEMINI_API_KEY` in the server process environment. The key is read on the server. Keep this local server bound to loopback. The optional `WORKFLOW_LAB_PORT` defaults to 8080.

Add industry profiles in `frontend/public/workflow-lab/squads.js` and corresponding role policies in `policies.js`. The simulated workflow uses browser storage and does not call the main runtime or GitHub.

### Per-agent models and terminals

Select an agent, open **Model & kết nối**, and choose its model, reasoning level, and shared or dedicated connection. Dedicated keys use `<PROVIDER>_API_KEY_<SQUAD>_<ROLE>` (for example `ANTHROPIC_API_KEY_CONSTRUCTION_REVIEW`). Role suffixes are `PRODUCE`, `REVIEW`, `DELIVERY`, `AUDIT`, `INTAKE`, and `SPECIALIST`. A missing dedicated key never falls back to the shared key. Model availability and supported parameters depend on provider access; adapter tests do not establish live model availability.

The terminal follows the selected agent and industry, with filters for submitted prompts, AI outputs, API lifecycle logs, and explicitly labelled simulated events. Logs stay in sessionStorage in the current tab, bounded to 300 entries across the office; export JSON or clear one agent's history. The terminal is a viewer, not an operating-system shell or an internal reasoning stream. No keys are included in API catalog responses or application-generated terminal entries. User-entered prompts and outputs are stored verbatim, so avoid including secrets in task descriptions.

Planning supports cancel/timeout handling and preserves edits made while a request is pending. Saved AI proposals retain agent/model provenance; this is not completion evidence. The six-agent workflow remains a browser simulation, separate from the main orchestration runtime.

Run the focused tests from the repository root:

```bash
node frontend/workflow-lab-server/tests/planner.mjs
node frontend/workflow-lab-server/tests/terminal.mjs
```

Provider adapters are verified with mocked responses. Live provider calls and visual browser QA are not covered by these tests. On local runs, configure the process environment; the Site-specific environment-variable instructions in the shared UI apply only to hosted Sites.

### MCP tools for Word, Notion and Outlook

The Workflow Lab and hosted Site use the same Streamable HTTP MCP gateway. The selected agent determines the tools listed and allowed by the gateway. Calls start from **Công cụ của …** and require confirmation of the exact arguments. The terminal keeps the call and result in the current tab. The planner and six-agent simulation do not invoke MCP tools automatically.

The FastAPI server exposes three MCP endpoints when `MCP_BRIDGE_TOKEN` is set to a strong secret of at least 32 characters:

| Endpoint | Tools | Backend environment |
| --- | --- | --- |
| `/mcp/word` | `word_list`, `word_read`, `word_create_draft` | `MCP_WORD_WORKSPACE` points to an existing directory. New DOCX drafts go in `.agent-drafts/`. |
| `/mcp/notion` | `notion_search`, `notion_read`, `notion_append` | `NOTION_API_KEY` belongs to an integration with access to the target pages. Appending requires write access. |
| `/mcp/outlook` | `outlook_list`, `outlook_create_draft` | `OUTLOOK_ACCESS_TOKEN` is a valid delegated Microsoft Graph token. Use `Mail.ReadBasic` to list metadata or `Mail.ReadWrite` to create drafts. |

Install backend dependencies and start FastAPI as shown above. Give the server a dedicated Word workspace. It reads DOCX text and tables and writes only new drafts. Outlook only creates drafts. OAuth sign-in and token refresh are managed outside AI Office in this version.

Set `MCP_SERVERS_JSON` in the Node server environment or the hosted Site's **Environment variables**. The Site panel generates a read-only example for the selected agent. The example below gives the first agent in **Vận hành chung** permission to list, read and create Word drafts:

```json
[
  {
    "id": "word",
    "name": "Word / DOCX",
    "url": "https://office-backend.example.com/mcp/word",
    "tokenEnv": "MCP_OFFICE_TOKEN",
    "grants": {
      "general:0": {
        "word_list": "read",
        "word_read": "read",
        "word_create_draft": "write"
      }
    }
  }
]
```

Set `MCP_OFFICE_TOKEN` as a server secret equal to the bridge token. Keep provider credentials in FastAPI's environment. Agent keys use `squad_id:role_index` (0–5). Grant individual tool names and classify each tool as `read` or `write`. Unknown tools are denied. A trusted external Streamable HTTP server can be configured using its endpoint and the same grants format. The client handles JSON and SSE responses and MCP session cleanup. It does not launch stdio processes.

For a local bridge, set `MCP_ALLOW_LOOPBACK=true` in the Node server process and use `http://127.0.0.1:8000/mcp/word`. The hosted Site needs a reachable HTTPS endpoint with valid TLS. When publishing the bridge behind a reverse proxy, route only the MCP paths needed by the Site. After configuring, select the agent, choose a connection, then click **Kiểm tra & tải công cụ**. Listing tools verifies the MCP bridge. Running a provider tool verifies the actual account connection.

```bash
python -m unittest discover -s tests -p test_mcp_office.py
node frontend/workflow-lab-server/tests/mcp.mjs
node frontend/workflow-lab-server/tests/mcp-e2e.mjs
```

The end-to-end check starts a local FastAPI bridge, then creates and reads a real DOCX through the JavaScript gateway. Notion and Graph API tests use mocked responses.
