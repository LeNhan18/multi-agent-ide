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

Serve the standalone browser demo from the repository root:

```bash
python -m http.server 8080 --bind 127.0.0.1 --directory frontend/public
```

Open <http://127.0.0.1:8080/workflow-lab/>. The lab simulates six-agent teams across multiple industries, including its message channels, status board, journal, and local checkpoint. The displayed API contract is a proposal. The demo uses browser storage and does not call the runtime or GitHub.

Add an industry in `frontend/public/workflow-lab/squads.js` with a label, a sample deliverable, and six role names in workflow-slot order. Demo checkpoints are scoped to the current protocol version.
