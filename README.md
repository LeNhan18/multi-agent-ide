# Multi-Agent IDE

Platform for orchestrating long-running AI agents (Supervisor, Coder, Reviewer) on a local workspace, with Git worktree isolation, Docker sandboxing, human-in-the-loop approval and cost guardrails.

See `CLAUDE.md` for architecture and engineering rules.

## Dev

```bash
docker compose -f docker/docker-compose.yml up -d
pip install -e ".[dev]"
uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
pytest
```
