"""API for build identity, recordings and diagnostic capture (isolated ports, tmp dirs)."""

import json
import socket
import time

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app, is_benign_close_reset
from backend.config import Config
from backend.recording.recorder import Recorder
from backend.recording.store import Store
from backend.telemetry.capture import DiagnosticCapture, read_capture
from backend.telemetry.hub import TelemetryHub
from backend.telemetry.receiver import UdpReceiver

from .packets import packet


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


_TX = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # one sender = one bound source


def send(port, data):
    _TX.sendto(data, ("127.0.0.1", port))


@pytest.fixture
def client(tmp_path):
    cfg = Config(udp_port=0, ui_rate_hz=60, extra_allowed_hosts=["testserver"])
    hub = TelemetryHub()
    rx = UdpReceiver(hub, "127.0.0.1", 0)
    rec = Recorder(Store(tmp_path / "recordings"), app_version="test")
    cap = DiagnosticCapture(tmp_path / "captures", app_version="test")
    app = create_app(cfg, hub, rx, recorder=rec, capture=cap)
    with TestClient(app) as c:
        c.rx, c.hub, c.rec, c.cap = rx, hub, rec, cap
        yield c


def feed(c, n, ts0=100, **kw):
    for i in range(n):
        send(c.rx.port, packet(TimestampMS=ts0 + i * 16, **kw))
    assert wait_for(lambda: c.hub.counters["received"] >= n)


def test_build_identity(client):
    b = client.get("/api/v1/build").json()
    assert b["server_instance_id"] == client.hub.server_instance_id
    assert len(b["backend_digest_full"]) == 64 and b["build_id"].startswith(b["app_version"] + "+")
    assert b["commit"] is None  # not claimed
    h = client.get("/api/v1/health").json()
    assert h["build"]["server_instance_id"] == b["server_instance_id"]
    caps = client.get("/api/v1/capabilities").json()
    assert caps["features"]["recording"] is True and caps["equal_timestamp_policy"] == "accept_changed"


def test_hello_carries_instance_and_build(client):
    with client.websocket_connect("/api/v1/stream") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["server_instance_id"] == client.hub.server_instance_id
        assert "ui_build_id" in hello and hello["build_id"]
        first = json.loads(ws.receive_text())
        assert first["status"]["recorder"]["state"] == "idle"


def test_recording_lifecycle_over_http(client):
    r = client.post("/api/v1/recordings/start", json={"title": "  Goliath  "})
    assert r.status_code == 201 and r.json()["state"] == "recording"
    rid = r.json()["recording_id"]
    assert client.post("/api/v1/recordings/start", json={}).status_code == 409
    feed(client, 40, Speed=20.0)
    assert wait_for(lambda: client.rec.samples == 40)
    assert client.get("/api/v1/health").json()["recorder"]["state"] == "recording"
    stop = client.post("/api/v1/recordings/stop")
    assert stop.status_code == 200 and stop.json()["state"] == "completed"
    assert client.post("/api/v1/recordings/stop").status_code == 409
    lst = client.get("/api/v1/recordings").json()
    assert lst["recordings"][0]["id"] == rid and lst["recordings"][0]["title"] == "Goliath"
    detail = client.get(f"/api/v1/recordings/{rid}").json()
    assert detail["samples"] == 40 and detail["chunks"][0]["status"] == "ok"
    p = client.patch(f"/api/v1/recordings/{rid}", json={"car_label": "Test car", "notes": "dry"}).json()
    assert p["car_label"] == "Test car" and p["notes"] == "dry"
    assert client.get(f"/api/v1/recordings/{rid}/verify").json()["ok"] is True
    ov = client.get(f"/api/v1/recordings/{rid}/overview?points=20").json()
    assert ov["samples_read"] == 40 and max(x for x in ov["speed_kmh_max"] if x is not None) == pytest.approx(72.0)
    assert client.get("/api/v1/recordings/nope").status_code == 404


def test_recording_control_needs_same_origin(client):
    r = client.post("/api/v1/recordings/start", json={}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403 and client.rec.state == "idle"


def test_recordings_unavailable_without_recorder():
    app = create_app(Config(udp_port=0, extra_allowed_hosts=["testserver"]), TelemetryHub())
    with TestClient(app) as c:
        assert c.get("/api/v1/recordings").status_code == 503
        assert c.get("/api/v1/capabilities").json()["features"]["recording"] is False


def test_diagnostic_capture_over_http(client, tmp_path):
    assert client.post("/api/v1/diagnostics/capture", json={"seconds": 1}).status_code == 422
    r = client.post("/api/v1/diagnostics/capture", json={"seconds": 30, "mask_sources": True})
    assert r.json()["state"] == "capturing"
    assert client.post("/api/v1/diagnostics/capture", json={"seconds": 30}).status_code == 409
    feed(client, 5, Speed=10.0)
    send(client.rx.port, packet(TimestampMS=100 + 4 * 16, Speed=10.0))  # identical duplicate
    send(client.rx.port, packet(TimestampMS=100 + 4 * 16, Speed=11.0))  # changed duplicate
    send(client.rx.port, packet(length=232))  # invalid
    assert wait_for(lambda: client.hub.counters["received"] == 8)
    client.post("/api/v1/diagnostics/capture/stop")
    client.cap.wait()
    st = client.get("/api/v1/diagnostics/capture").json()
    assert st["capture"]["state"] == "idle"
    s = st["captures"][0]
    assert s["datagrams"] == 8
    assert s["equal_timestamp"] == {"identical": 1, "tail_only": 0, "non_finite": 0, "changed": 1}
    # default policy: the repeat is rejected, the new content with the same timestamp kept
    assert s["outcomes"] == {"accepted": 6, "duplicates": 1, "rejected:too_short": 1}
    header, records = read_capture(tmp_path / "captures" / s["file"])
    assert header["sources_masked"] is True and {r["src"] for r in records} == {"source-1"}
    assert [r["i"] for r in records] == list(range(8))  # arrival order


def test_capture_size_limit_and_quota(tmp_path):
    cap = DiagnosticCapture(tmp_path, max_bytes=2000, keep_files=2)
    for k in range(3):
        cap.start(30)
        for i in range(20):
            cap.tap(packet(TimestampMS=i), "a", "accepted")
        cap.wait()
        assert cap.status()["last_result"]["stop_reason"] == "size limit reached"
        time.sleep(1.1)  # file names carry the start second
    assert len(list(tmp_path.glob("capture-*.jsonl.gz"))) == 2


def test_benign_proactor_reset_is_recognised_exactly():
    class H:
        def __repr__(self):
            return "<Handle _ProactorBasePipeTransport._call_connection_lost()>"

    assert is_benign_close_reset({"exception": ConnectionResetError(10054, "reset"), "handle": H()})
    assert not is_benign_close_reset({"exception": ConnectionResetError(10054, "reset"), "handle": "<Handle other()>"})
    assert not is_benign_close_reset({"exception": OSError("x"), "handle": H()})


def test_replay_frames_window(client):
    rid = client.post("/api/v1/recordings/start", json={}).json()["recording_id"]
    for i in range(30):
        send(client.rx.port, packet(TimestampMS=1000 + i * 16, Speed=float(i)))
    send(client.rx.port, packet(TimestampMS=1000 + 29 * 16, Speed=99.0))  # equal ts, new content
    assert wait_for(lambda: client.rec.samples == 31)
    client.post("/api/v1/recordings/stop")
    r = client.get(f"/api/v1/recordings/{rid}/frames?from=0&to=60", headers={"accept-encoding": "gzip"})
    assert r.status_code == 200 and r.headers.get("content-encoding") == "gzip"
    body = r.json()
    rows = body["rows"]
    assert [x["i"] for x in rows] == list(range(31))  # sample index orders equal timestamps
    assert [x["frame"]["normalized"]["speed_mps"] for x in rows[:3]] == [0.0, 1.0, 2.0]
    assert rows[-1]["frame"]["game_timestamp_ms"] == rows[-2]["frame"]["game_timestamp_ms"]
    assert rows[-1]["frame"]["normalized"]["speed_mps"] == 99.0
    assert all(a["el"] <= b["el"] for a, b in zip(rows, rows[1:]))
    f = rows[0]["frame"]
    assert f["source"] == "recording" and f["recording_id"] == rid and f["stream_segment_id"]
    assert body["truncated"] is False
    assert client.get(f"/api/v1/recordings/{rid}/frames?from=0&to=61").status_code == 422
    assert client.get(f"/api/v1/recordings/{rid}/frames?from=5&to=4").status_code == 422
    capped = client.get(f"/api/v1/recordings/{rid}/frames?from=0&to=60&max=10").json()
    assert len(capped["rows"]) == 10 and capped["truncated"] is True
    # replay reads never change the live hub
    assert client.hub.mode == "live"


def test_lab_endpoints_and_annotations(client):
    rid = client.post("/api/v1/recordings/start", json={}).json()["recording_id"]
    for i in range(60):
        send(client.rx.port, packet(TimestampMS=5000 + i * 16, Speed=float(i), Accel=255, Gear=3))
    assert wait_for(lambda: client.rec.samples == 60)
    client.post("/api/v1/recordings/stop")
    meta = client.get(f"/api/v1/recordings/{rid}/lab").json()
    assert meta["samples"] == 60 and {c["id"] for c in meta["channels"]} >= {"speed", "g_lat", "pos_x"}
    sp = next(c for c in meta["channels"] if c["id"] == "speed")
    assert sp["unit"] == "km/h" and sp["valid_fraction"] == 1.0
    s = client.get(f"/api/v1/recordings/{rid}/lab/series?channels=speed,throttle&points=50").json()
    assert s["channels"]["speed"]["stats"]["max"] == pytest.approx(59 * 3.6)
    assert s["samples_in_range"] == 60
    assert client.get(f"/api/v1/recordings/{rid}/lab/series?channels=speed,nope").status_code == 422
    nine = ",".join(["speed"] * 9)
    assert client.get(f"/api/v1/recordings/{rid}/lab/series?channels={nine}").status_code == 422
    for part in ("summary", "gg", "route", "power"):
        assert client.get(f"/api/v1/recordings/{rid}/lab/{part}").status_code == 200
    csv = client.get(f"/api/v1/recordings/{rid}/lab/export.csv?channels=speed")
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    assert len(csv.text.strip().splitlines()) == 61
    r = client.post(f"/api/v1/recordings/{rid}/annotations", json={"elapsed_s": 0.5, "text": "  late apex  "})
    assert r.status_code == 201 and r.json()["text"] == "late apex"
    assert client.get(f"/api/v1/recordings/{rid}/lab").json()["annotations"][0]["text"] == "late apex"
    assert client.post(f"/api/v1/recordings/{rid}/annotations", json={"elapsed_s": 1, "text": " "}).status_code == 422
    assert client.post(f"/api/v1/recordings/{rid}/annotations", json={"elapsed_s": 1, "text": "x"}, headers={"origin": "http://evil.example"}).status_code == 403
    aid = r.json()["id"]
    assert client.delete(f"/api/v1/recordings/{rid}/annotations/{aid}").status_code == 200
    assert client.delete(f"/api/v1/recordings/{rid}/annotations/{aid}").status_code == 404
    assert client.get("/api/v1/capabilities").json()["features"]["analysis"] is True
