from __future__ import annotations

import secrets

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status

from backend.app.core.events import AgentEventPayload

ALLOWED_ORIGINS = {"http://localhost:3000", "http://127.0.0.1:3000"}
SESSION_TOKEN = secrets.token_urlsafe(32)

app = FastAPI(title="Multi-Agent IDE")


@app.on_event("startup")
async def _print_token() -> None:
    print(f"[multi-agent-ide] session token: {SESSION_TOKEN}")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.websocket("/ws/events")
async def events(ws: WebSocket) -> None:
    origin = ws.headers.get("origin")
    if origin not in ALLOWED_ORIGINS or ws.query_params.get("token") != SESSION_TOKEN:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    await ws.accept()
    try:
        while True:
            raw = await ws.receive_json()
            event = AgentEventPayload.model_validate(raw)
            await ws.send_json(event.model_dump(mode="json"))
    except WebSocketDisconnect:
        return
