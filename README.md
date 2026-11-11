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
