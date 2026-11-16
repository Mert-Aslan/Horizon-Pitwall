"""HTTP + WebSocket API and static UI hosting."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import json
import logging
import os
import socket
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..config import APP_NAME, APP_VERSION, Config
from ..telemetry import schema
from ..telemetry.hub import API_VERSION, TelemetryHub
from ..telemetry.receiver import UdpReceiver
from ..telemetry.synth import DEMO_CAR_ORDINAL, DemoGenerator

log = logging.getLogger("pitwall.api")

ROOT = Path(__file__).resolve().parents[2]
# PITWALL_UI_DIST lets tests serve another UI build (e.g. a before/after comparison).
DIST = Path(os.environ["PITWALL_UI_DIST"]) if os.environ.get("PITWALL_UI_DIST") else ROOT / "frontend" / "dist"

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)


def _local_hosts(cfg: Config) -> list[str]:
    hosts = {"localhost", "127.0.0.1", "[::1]", "::1"}
    if cfg.lan:
        with contextlib.suppress(OSError):
            name = socket.gethostname()
            hosts.add(name)
            hosts.update(socket.gethostbyname_ex(name)[2])
    if cfg.web_host not in ("0.0.0.0", "::"):
        hosts.add(cfg.web_host)
    hosts.update(cfg.extra_allowed_hosts)
    return sorted(hosts)


def _is_loopback(host: Optional[str]) -> bool:
    if not host:
        return False
    if host in ("testclient", "localhost"):
        return True
    with contextlib.suppress(ValueError):
        return ipaddress.ip_address(host).is_loopback
    return False


def _origin_ok(headers, cfg: Config) -> bool:
    """Same-origin check for state-changing requests and WebSockets. Requests without an
    Origin header (curl, scripts) are allowed; browsers always send one for these."""
    origin = headers.get("origin")
    if origin is None:
        return True
    host = headers.get("host", "")
    parts = urlsplit(origin)
    return parts.scheme in ("http", "https") and parts.netloc == host


class DemoRequest(BaseModel):
    enabled: bool


class FrameSignal:
    """Wakes every stream coroutine when the receiver thread accepts a frame."""

    def __init__(self) -> None:
        self._ev = asyncio.Event()

    def notify(self) -> None:
        ev, self._ev = self._ev, asyncio.Event()
        ev.set()

    async def wait(self, timeout: float) -> None:
        ev = self._ev
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(ev.wait(), timeout)


class UiClient:
    def __init__(self, cid: int, peer: str) -> None:
        self.id = cid
        self.peer = peer
        self.frames_sent = 0
        self.frames_skipped = 0  # display frames skipped because the client is slow
        self.connected_at = time.time()


def create_app(cfg: Config, hub: TelemetryHub, receiver: Optional[UdpReceiver] = None, demo: Optional[DemoGenerator] = None) -> FastAPI:
    demo = demo or DemoGenerator(hub)
    clients: dict[int, UiClient] = {}
    next_id = [0]
    signal_box: dict[str, FrameSignal] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        signal = signal_box["s"] = FrameSignal()
        hub.on_frame = lambda: loop.call_soon_threadsafe(signal.notify)
        if receiver is not None:
            receiver.start()
        if cfg.demo:
            hub.set_mode("demo")
            demo.start()
        yield
        hub.on_frame = None
        demo.stop()
        if receiver is not None:
            receiver.stop()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=_local_hosts(cfg))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            if not _origin_ok(request.headers, cfg):
                return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
            client = request.client.host if request.client else None
            if cfg.lan and not cfg.lan_allow_control and not _is_loopback(client):
                return JSONResponse({"error": "control from LAN clients is disabled"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    # ------------------------------------------------------------------ API
    @app.get("/api/v1/health")
    def health():
        st = hub.status()
        ok = st["receiver_state"] == "listening" or st["mode"] == "demo"
        return {
            "status": "ok" if ok else "degraded",
            "app": APP_NAME,
            "app_version": APP_VERSION,
            "api_version": API_VERSION,
            "receiver": {"state": st["receiver_state"], "error": st["receiver_error"], "address": st["receiver_address"]},
            "connection_state": st["connection_state"],
            "last_valid_packet_age_ms": st["packet_age_ms"],
            "mode": st["mode"],
            "recorder": {"available": False, "note": "session recording is not part of this build yet"},
            "ui_clients": len(clients),
        }

    @app.get("/api/v1/telemetry/latest")
    def latest():
        return hub.latest()

    @app.get("/api/v1/telemetry/history")
    def history(seconds: float = Query(30.0, ge=1.0, le=120.0)):
        return hub.history_payload(seconds)

    @app.get("/api/v1/capabilities")
    def capabilities():
        return {
            "app_version": APP_VERSION,
            "api_version": API_VERSION,
            "schema": schema.describe(),
            "features": {
                "live": True,
                "demo": True,
                "raw_inspector": True,
                "recording": False,
                "replay": False,
                "analysis": False,
                "car_catalog": False,
            },
            "units": {"speed": "km/h (derived from m/s)", "power": "kW / hp (mechanical) derived from W", "torque": "N*m", "boost": "psi above atmospheric"},
            "verification": {
                "packet_length_324": "documented",
                "trailing_byte": "undocumented; kept as opaque tail (P-1)",
                "byte_order": "assumed little-endian (P-2)",
                "gear_reverse_neutral": "unverified; gear 0 shown as raw code (P-3)",
                "tire_temperature_unit": "undocumented; shown without unit conversion (P-4)",
                "car_class_letters": "only 0=D and 7=X documented; C/B/A/S1/S2/R are convention (P-5)",
                "real_game_validation": "pending",
            },
            "demo_car_ordinal": DEMO_CAR_ORDINAL,
            "ui_rate_hz": cfg.ui_rate_hz,
            "raw_rate_hz": cfg.raw_rate_hz,
            "udp": {"host": cfg.udp_host, "port": receiver.port if receiver else cfg.udp_port},
            "lan": cfg.lan,
        }

    @app.get("/api/v1/diagnostics")
    def diagnostics():
        d = hub.diagnostics()
        d["ui_clients"] = [vars(c) for c in clients.values()]
        d["config"] = {k: v for k, v in cfg.public().items() if k not in ("data_dir",)}
        return d

    @app.post("/api/v1/demo")
    def set_demo(req: DemoRequest):
        if req.enabled:
            hub.set_mode("demo")
            demo.start()
        else:
            demo.stop()
            hub.set_mode("live")
        return {"mode": hub.mode}

    # ------------------------------------------------------------ stream
    @app.websocket("/api/v1/stream")
    async def stream(ws: WebSocket):
        if not _origin_ok(ws.headers, cfg):
            await ws.close(code=1008)
            return
        await ws.accept()
        next_id[0] += 1
        client = UiClient(next_id[0], f"{ws.client.host}:{ws.client.port}" if ws.client else "?")
        clients[client.id] = client
        opts = {"raw": False, "force": True}
        signal = signal_box.get("s") or FrameSignal()

        async def reader():
            while True:
                text = await ws.receive_text()
                if len(text) > 1024:
                    continue
                with contextlib.suppress(ValueError, AttributeError):
                    msg = json.loads(text)
                    if msg.get("type") == "subscribe":
                        opts["raw"] = bool(msg.get("raw"))
                        opts["force"] = True
                        signal.notify()
                    elif msg.get("type") == "resync":
                        opts["force"] = True
                        signal.notify()

        reader_task = asyncio.create_task(reader())
        try:
            await ws.send_text(
                json.dumps({"type": "hello", "api_version": API_VERSION, "schema_version": schema.SCHEMA_VERSION, "app_version": APP_VERSION, "ui_rate_hz": cfg.ui_rate_hz})
            )
            period = 1.0 / cfg.ui_rate_hz
            raw_period = 1.0 / cfg.raw_rate_hz
            last_seq = -1
            last_send = 0.0
            last_raw = 0.0
            # Event driven + rate limited: a new frame is sent as soon as it arrives if the
            # client's last send is at least ~one period ago; frames in between are
            # skipped (latest wins). No fixed sleep tick, so Windows' coarse timer
            # resolution does not cap the rate. Heartbeat (status only) at 2 Hz.
            while not reader_task.done():
                now = time.monotonic()
                since = now - last_send
                new = hub.sequence != last_seq
                if (new and since >= period * 0.9) or opts["force"] or since >= 0.5:
                    want_raw = opts["raw"] and now - last_raw >= raw_period
                    seq, msg = hub.message(include_raw=want_raw)
                    if last_seq >= 0 and seq > last_seq + 1:
                        client.frames_skipped += seq - last_seq - 1
                    await asyncio.wait_for(ws.send_text(msg), timeout=2.0)
                    client.frames_sent += 1
                    opts["force"] = False
                    last_seq, last_send = seq, now
                    if want_raw:
                        last_raw = now
                    continue
                # Wait for the next frame, or until the pending frame becomes due.
                timeout = max(0.002, (period - since) if new else (0.5 - since))
                await signal.wait(timeout)
        except (WebSocketDisconnect, asyncio.TimeoutError, RuntimeError, ConnectionError):
            pass
        finally:
            reader_task.cancel()
            with contextlib.suppress(BaseException):
                await reader_task
            clients.pop(client.id, None)
            with contextlib.suppress(Exception):
                await ws.close()

    # ------------------------------------------------------------ static UI
    index = DIST / "index.html"
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/", include_in_schema=False)
    def root():
        if not index.exists():
            return PlainTextResponse(
                "Horizon Pitwall: the UI bundle is missing (frontend/dist).\n"
                "Build it once with:  cd frontend && npm ci && npm run build\n"
                "The API is available under /api/v1/.",
                status_code=503,
            )
        return FileResponse(index, headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})

    @app.get("/favicon.svg", include_in_schema=False)
    def favicon():
        f = DIST / "favicon.svg"
        return FileResponse(f) if f.exists() else PlainTextResponse("", status_code=404)

    return app
