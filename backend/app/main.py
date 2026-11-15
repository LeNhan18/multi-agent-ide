from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from backend.app.api.mcp import router as mcp_router
from backend.app.core.bus import EventBus

DEFAULT_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000,http://127.0.0.1:8080"
ALLOWED_ORIGINS = {o.strip() for o in os.environ.get("WS_ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",") if o.strip()}
SESSION_TOKEN = os.environ.get("AI_OFFICE_SESSION_TOKEN") or secrets.token_urlsafe(32)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.bus = EventBus()
    print(f"[multi-agent-ide] session token: {SESSION_TOKEN}")
    yield


app = FastAPI(title="Multi-Agent IDE", lifespan=lifespan)
app.include_router(mcp_router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    """Server-to-client stream of AgentEventPayload. Clients cannot inject events."""
    origin = ws.headers.get("origin")
    token = ws.query_params.get("token", "")
    if origin not in ALLOWED_ORIGINS or not secrets.compare_digest(token.encode(), SESSION_TOKEN.encode()):
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    bus: EventBus = ws.app.state.bus
    await ws.accept()
    with bus.subscribe() as queue:
        for event in bus.recent:
            await ws.send_json(event.model_dump(mode="json"))

        async def forward() -> None:
            while True:
                event = await queue.get()
                await ws.send_json(event.model_dump(mode="json"))

        async def drain_client() -> None:
            while True:
                await ws.receive_text()  # keep-alive pings; content is ignored

        tasks = {asyncio.create_task(forward()), asyncio.create_task(drain_client())}
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        except WebSocketDisconnect:
            pass
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
