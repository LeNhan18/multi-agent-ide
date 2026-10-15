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
