import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import main
from backend.app.core.events import AgentEventPayload, EventType

ORIGIN = {"origin": "http://127.0.0.1:8080"}


def event(n: int) -> AgentEventPayload:
    return AgentEventPayload(event_type=EventType.AGENT_STATE_CHANGE, agent_id="coder", task_id="t1", payload={"n": n})


def test_ws_replays_recent_and_streams_new_events():
    with TestClient(main.app) as client:
        bus = main.app.state.bus
        client.portal.call(bus.publish, event(1))
        with client.websocket_connect(f"/ws/events?token={main.SESSION_TOKEN}", headers=ORIGIN) as ws:
            assert ws.receive_json()["payload"] == {"n": 1}
            client.portal.call(bus.publish, event(2))
            received = ws.receive_json()
            assert received["payload"] == {"n": 2} and received["event_type"] == "AGENT_STATE_CHANGE"


@pytest.mark.parametrize("query,headers", [
    ("token=wrong", ORIGIN),
    (f"token={main.SESSION_TOKEN}", {"origin": "https://evil.example"}),
    (f"token={main.SESSION_TOKEN}", {}),
])
def test_ws_rejects_bad_token_or_origin(query, headers):
    with TestClient(main.app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/events?{query}", headers=headers) as ws:
                ws.receive_json()


async def test_slow_subscriber_drops_oldest():
    from backend.app.core.bus import EventBus

    bus = EventBus(queue_size=2)
    with bus.subscribe() as queue:
        for n in range(3):
            await bus.publish(event(n))
        assert [queue.get_nowait().payload["n"] for _ in range(2)] == [1, 2]
