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

The short task form works without credentials. To enable AI planning, provide `OPENAI_API_KEY` in the server process environment. The key is read on the server. Keep this local server bound to loopback. The optional `WORKFLOW_LAB_PORT` defaults to 8080.

Add industry profiles in `frontend/public/workflow-lab/squads.js` and corresponding role policies in `policies.js`. The simulated workflow uses browser storage and does not call the main runtime or GitHub.
