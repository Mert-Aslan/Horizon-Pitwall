"""UDP lifecycle and API tests. Only ephemeral, isolated ports are used; nothing is sent
to the game's default port 5607."""

import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.config import Config
from backend.telemetry.hub import TelemetryHub
from backend.telemetry.receiver import UdpReceiver

from .packets import packet


def wait_for(cond, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def receiver():
    hub = TelemetryHub()
    rx = UdpReceiver(hub, "127.0.0.1", 0)
    assert rx.start()
    yield rx
    rx.stop()


def send(port, data):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.sendto(data, ("127.0.0.1", port))
    s.close()


def test_udp_receive_valid_and_invalid(receiver):
    hub = receiver.hub
    send(receiver.port, packet(TimestampMS=1))
    send(receiver.port, packet(length=323, TimestampMS=2))
    assert wait_for(lambda: hub.counters["received"] == 2)
    assert hub.counters["accepted"] == 1 and hub.counters["rejected"] == 1
    assert hub.status()["connection_state"] == "receiving"


def test_port_in_use_is_visible_error():
    blocker = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blocker.bind(("127.0.0.1", 0))
    port = blocker.getsockname()[1]
    try:
        hub = TelemetryHub()
        rx = UdpReceiver(hub, "127.0.0.1", port)
        assert rx.start() is False
        st = hub.status()
        assert st["connection_state"] == "error"
        assert "cannot bind" in st["receiver_error"]
    finally:
        blocker.close()


def test_shutdown_and_restart_releases_port():
    hub = TelemetryHub()
    rx = UdpReceiver(hub, "127.0.0.1", 0)
    assert rx.start()
    port = rx.port
    rx.stop()
    assert hub.receiver_state == "stopped"
    rx2 = UdpReceiver(hub, "127.0.0.1", port)
    assert rx2.start()
    send(port, packet(TimestampMS=5))
    assert wait_for(lambda: hub.counters["accepted"] == 1)
    rx2.stop()


# ------------------------------------------------------------------ API


@pytest.fixture
def client():
    cfg = Config(udp_port=0, ui_rate_hz=60, extra_allowed_hosts=["testserver"])
    hub = TelemetryHub()
    rx = UdpReceiver(hub, "127.0.0.1", 0)
    app = create_app(cfg, hub, rx)
    with TestClient(app) as c:
        c.rx = rx
        yield c


def test_health_and_capabilities(client):
    h = client.get("/api/v1/health").json()
    assert h["receiver"]["state"] == "listening"
    assert h["connection_state"] == "waiting"
    caps = client.get("/api/v1/capabilities").json()
    assert caps["schema"]["documented_packet_length"] == 324
    assert caps["features"]["recording"] is False
    assert caps["verification"]["real_game_validation"] == "pending"


def test_latest_after_udp(client):
    send(client.rx.port, packet(TimestampMS=77, Speed=0.0))
    assert wait_for(lambda: client.rx.hub.counters["accepted"] == 1)
    body = client.get("/api/v1/telemetry/latest").json()
    assert body["frame"]["game_timestamp_ms"] == 77
    assert body["frame"]["derived"]["speed_kmh"] == 0.0
    assert body["frame"]["connection_state"] == "receiving"


def test_untrusted_host_header_refused(client):
    r = client.get("/api/v1/health", headers={"host": "attacker.example"})
    assert r.status_code == 400


def test_cross_origin_post_refused(client):
    r = client.post("/api/v1/demo", json={"enabled": True}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403
    assert client.rx.hub.mode == "live"


def test_demo_toggle_same_origin(client):
    r = client.post("/api/v1/demo", json={"enabled": True}, headers={"origin": "http://testserver"})
    assert r.json()["mode"] == "demo"
    assert wait_for(lambda: client.rx.hub.status()["connection_state"] == "receiving")
    r = client.post("/api/v1/demo", json={"enabled": False})
    assert r.json()["mode"] == "live"
    assert client.rx.hub.status()["connection_state"] == "waiting"


def test_unknown_paths_404(client):
    assert client.get("/api/telemetry").status_code == 404
    assert client.get("/index.html.bak").status_code == 404


def test_stream_hello_frames_and_raw_subscription(client):
    with client.websocket_connect("/api/v1/stream") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["api_version"] == 1
        first = json.loads(ws.receive_text())
        assert first["type"] == "frame" and first["frame"] is None
        assert first["status"]["connection_state"] == "waiting"
        send(client.rx.port, packet(TimestampMS=10))
        msg = None
        for _ in range(100):
            msg = json.loads(ws.receive_text())
            if msg["frame"]:
                break
        assert msg["frame"]["game_timestamp_ms"] == 10 and "raw" not in msg["frame"]
        ws.send_text(json.dumps({"type": "subscribe", "raw": True}))
        for _ in range(100):
            msg = json.loads(ws.receive_text())
            if msg["frame"] and "raw" in msg["frame"]:
                break
        assert msg["frame"]["raw"]["TimestampMS"] == 10


def test_stream_rejects_cross_origin(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/api/v1/stream", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_text()
