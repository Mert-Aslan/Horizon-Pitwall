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

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from ..analysis import lab, perf
from ..buildinfo import BuildInfo
from ..catalog import CarCatalog, CatalogError, dumps_export, seen_cars
from ..config import APP_NAME, APP_VERSION, Config
from ..recording.recorder import Recorder, RecorderBusy, RecorderIdle
from ..recording.store import EDITABLE, decode_datagram
from ..telemetry import decode as dec
from ..telemetry import schema
from ..telemetry.capture import DiagnosticCapture
from ..telemetry.hub import API_VERSION, TelemetryHub
from ..telemetry.receiver import UdpReceiver
from ..telemetry.synth import DEMO_CAR_ORDINAL, DemoGenerator

log = logging.getLogger("pitwall.api")

# Application-level flow control for the UI stream. OS socket buffers can hold several MB,
# so a slow reader (weak Wi-Fi, busy phone) would otherwise see data tens of seconds old
# while the server believes every send succeeded. A client that opts in acknowledges
# every FLOW_ACK_EVERY messages; at most FLOW_WINDOW messages may be unacknowledged
# (0.2 s at 30 Hz); a slow reader therefore sees at most about window / its own read
# rate of backlog (measured: 1.3 s at 5 msg/s instead of 30-50 s without it). While
# the window is full, frames are withheld (latest wins).
FLOW_WINDOW = 6
REPLAY_MAX_WINDOW_S = 60.0
REPLAY_MAX_ROWS = 8000
FLOW_ACK_EVERY = 2

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


class StartRecording(BaseModel):
    title: Optional[str] = None


class RecordingPatch(BaseModel):
    title: Optional[str] = None
    notes: Optional[str] = None
    car_label: Optional[str] = None
    setup_label: Optional[str] = None


class CarEntry(BaseModel):
    name: str
    year: Optional[int] = None
    variant: Optional[str] = None
    notes: Optional[str] = None


class ArmRequest(BaseModel):
    kind: str


class AnnotationRequest(BaseModel):
    elapsed_s: float
    text: str


class CaptureRequest(BaseModel):
    seconds: float = 30
    mask_sources: bool = False


def is_benign_close_reset(context: dict) -> bool:
    """Windows Proactor: when a browser drops a TCP connection abruptly (tab closed,
    reload, sleep), asyncio's ``_call_connection_lost`` calls ``shutdown()`` on a socket
    the peer already reset and logs WinError 10054 with a traceback. The connection is
    already gone; nothing is lost. Only this exact case is recognised."""
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    where = repr(context.get("handle")) + str(context.get("message", ""))
    return "_call_connection_lost" in where


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
        # Accepted frames not sent because of the UI rate limit (e.g. 60 Hz in, 30 Hz out).
        # This is normal downsampling, not packet loss and not a slow client.
        self.ui_rate_limited_frames = 0
        self.slow_sends = 0  # a single send took longer than 250 ms (slow client / socket)
        self.send_ms_max = 0.0
        self.flow_control = False
        self.flow_withheld_frames = 0  # frames not sent because the client had not caught up
        self.flow_pauses = 0
        self.connected_at = time.time()


def create_app(
    cfg: Config,
    hub: TelemetryHub,
    receiver: Optional[UdpReceiver] = None,
    demo: Optional[DemoGenerator] = None,
    recorder: Optional[Recorder] = None,
    capture: Optional[DiagnosticCapture] = None,
    build: Optional[BuildInfo] = None,
    live_perf: Optional[perf.LivePerf] = None,
    catalog: Optional[CarCatalog] = None,
    paths: Optional[dict] = None,
) -> FastAPI:
    demo = demo or DemoGenerator(hub)
    build = build or BuildInfo(APP_VERSION, API_VERSION, schema.SCHEMA_VERSION, DIST)
    build.server_instance_id = hub.server_instance_id
    stream_stats = {"ws_send_timeouts": 0, "ws_closed": 0, "ws_errors": 0, "asyncio_close_resets": 0}
    if recorder is not None:
        hub.sample_sinks.append(recorder.offer)
        hub.event_sinks.append(recorder.on_event)
        hub.recorder_status = lambda: {k: v for k, v in recorder.status().items() if k != "last_result"}
    if capture is not None:
        hub.capture = capture
    live_perf = live_perf or perf.LivePerf()
    hub.sample_sinks.append(live_perf.offer)
    hub.extra_status["perf"] = lambda: {k: v for k, v in live_perf.status().items() if k != "history"}
    clients: dict[int, UiClient] = {}
    next_id = [0]
    signal_box: dict[str, FrameSignal] = {}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        loop = asyncio.get_running_loop()
        default_handler = loop.get_exception_handler()

        def exception_handler(lp, context):
            if is_benign_close_reset(context):
                stream_stats["asyncio_close_resets"] += 1
                # visible, one line, no traceback: the peer reset an already closing connection
                log.info("event=client_reset_on_close the browser reset a closing connection (WinError 10054); nothing was lost")
                return
            if default_handler is not None:
                default_handler(lp, context)
            else:
                lp.default_exception_handler(context)

        loop.set_exception_handler(exception_handler)
        signal = signal_box["s"] = FrameSignal()
        hub.on_frame = lambda: loop.call_soon_threadsafe(signal.notify)
        if receiver is not None:
            receiver.start()
        if cfg.demo:
            hub.set_mode("demo")
            demo.start()
        yield
        hub.on_frame = None
        if recorder is not None:
            recorder.shutdown()
        demo.stop()
        if receiver is not None:
            receiver.stop()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url="/api/v1/openapi.json")
    # Replay windows are large, repetitive JSON; gzip cuts them ~10x. WebSockets are not affected.
    app.add_middleware(GZipMiddleware, minimum_size=4096)
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
            "recorder": st["recorder"] if recorder is not None else {"available": False},
            "ui_clients": len(clients),
            "build": {k: v for k, v in build.as_dict().items() if k in ("build_id", "server_instance_id", "ui", "started_at_utc")},
        }

    @app.get("/api/v1/build")
    def build_info():
        return build.as_dict()

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
                "recording": recorder is not None,
                "diagnostic_capture": capture is not None,
                "replay": recorder is not None,
                "analysis": recorder is not None,
                "car_catalog": catalog is not None,
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
            "lan_allow_control": cfg.lan_allow_control,
            "server_instance_id": hub.server_instance_id,
            "equal_timestamp_policy": hub.equal_timestamp_policy,
        }

    @app.get("/api/v1/diagnostics")
    def diagnostics():
        d = hub.diagnostics()
        d["ui_clients"] = [vars(c) for c in clients.values()]
        d["stream"] = dict(stream_stats)
        if recorder is not None:
            d["recorder"] = recorder.status() | {
                "mode_mismatch": recorder.mode_mismatch,
                "flush_ms_max": round(recorder.flush_ms_max, 1),
                "chunks_written": recorder.chunks_written,
            }
        d["build"] = build.as_dict()
        d["paths"] = paths or {}
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

    # ------------------------------------------------------------ recordings
    def need_recorder() -> Recorder:
        if recorder is None:
            raise HTTPException(503, "recording is not available in this instance")
        return recorder

    @app.get("/api/v1/recordings")
    def list_recordings():
        r = need_recorder()
        return {"recorder": r.status(), "recordings": r.store.list(), "reconciled_at_startup": r.reconciled}

    @app.post("/api/v1/recordings/start", status_code=201)
    def start_recording(req: StartRecording):
        r = need_recorder()
        try:
            st = r.start(hub.mode, (req.title or "").strip()[:120] or None)
        except RecorderBusy as e:
            return JSONResponse({"error": "already_recording", "state": str(e), "recorder": r.status()}, status_code=409)
        if st["state"] == "failed":
            return JSONResponse({"error": "start_failed", "recorder": st}, status_code=500)
        return st

    @app.post("/api/v1/recordings/stop")
    def stop_recording():
        r = need_recorder()
        try:
            return r.stop("user")
        except RecorderIdle as e:
            return JSONResponse({"error": "not_recording", "state": str(e), "recorder": r.status()}, status_code=409)

    @app.get("/api/v1/recordings/{rec_id}")
    def get_recording(rec_id: str):
        rec = need_recorder().store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        return rec

    @app.patch("/api/v1/recordings/{rec_id}")
    def patch_recording(rec_id: str, req: RecordingPatch):
        store = need_recorder().store
        changes = {k: (v.strip()[:2000] if isinstance(v, str) else v) for k, v in req.model_dump(exclude_unset=True).items() if k in EDITABLE}
        if not store.update_metadata(rec_id, changes):
            raise HTTPException(404, "recording not found")
        return store.get(rec_id)

    @app.get("/api/v1/recordings/{rec_id}/verify")
    def verify_recording(rec_id: str):
        try:
            return need_recorder().store.verify(rec_id)
        except KeyError:
            raise HTTPException(404, "recording not found") from None

    @app.get("/api/v1/recordings/{rec_id}/frames")
    def recording_frames(
        rec_id: str,
        t_from: float = Query(0.0, alias="from", ge=0.0),
        t_to: float = Query(..., alias="to", ge=0.0),
        max_rows: int = Query(REPLAY_MAX_ROWS, alias="max", ge=1, le=REPLAY_MAX_ROWS),
    ):
        """Decoded frames of a time window for client-side replay. The window is bounded
        (at most REPLAY_MAX_WINDOW_S seconds and REPLAY_MAX_ROWS rows); only chunks that
        overlap it are opened. Time base: ``el`` = seconds since recording start
        (monotonic); ``i`` orders samples, also when game timestamps are equal."""
        store = need_recorder().store
        rec = store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        if t_to < t_from or t_to - t_from > REPLAY_MAX_WINDOW_S:
            raise HTTPException(422, f"window must be 0..{REPLAY_MAX_WINDOW_S:.0f} s long")
        seg_ids = {s["segment_index"]: s["stream_segment_id"] for s in rec["segments_detail"]}
        rows = []
        truncated = False
        for line in store.iter_lines(rec_id, t_from, t_to):
            if len(rows) >= max_rows:
                truncated = True
                break
            res = dec.decode(decode_datagram(line))
            if not res.ok:
                continue
            n, d, q = dec.normalize(res.raw)
            q["tail_hex"] = res.tail.hex()
            q["packet_length"] = res.length
            rows.append({
                "i": line["i"],
                "el": line["el"],
                "frame": {
                    "schema_version": schema.SCHEMA_VERSION,
                    "api_version": API_VERSION,
                    "recording_id": rec_id,
                    "stream_segment_id": seg_ids.get(line["seg"], f"segment-{line['seg']}"),
                    "session_id": seg_ids.get(line["seg"], f"segment-{line['seg']}"),
                    "sequence": line["sq"],
                    "sample_index": line["i"],
                    "game_timestamp_ms": line["ts"],
                    "recording_elapsed_s": line["el"],
                    "received_at": line["w"],
                    "mode": rec["mode"],
                    "source": "recording",
                    "normalized": n,
                    "derived": d,
                    "quality": q,
                },
            })
        duration = rec["duration_s"] or (rec["chunks"][-1]["t_end_s"] if rec["chunks"] else 0.0) or 0.0
        return {
            "recording_id": rec_id,
            "from": t_from,
            "to": t_to,
            "duration_s": duration,
            "status": rec["status"],
            "truncated": truncated,
            "rows": rows,
        }

    # ------------------------------------------------------- Telemetry Lab (M4)
    lab_cache = lab.LabCache()

    def lab_columns(rec_id: str) -> tuple[dict, lab.Columns]:
        store = need_recorder().store
        rec = store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        col = lab_cache.get(rec_id, rec["samples"], lambda: lab.Columns.build(store.iter_lines(rec_id)))
        return rec, col

    def lab_range(col: lab.Columns, t_from: Optional[float], t_to: Optional[float]) -> tuple[float, float]:
        end = col.el[-1] if len(col) else 0.0
        a = 0.0 if t_from is None else max(0.0, t_from)
        b = end if t_to is None else min(max(t_to, a), end if end > a else a)
        return a, b

    def lab_ids(channels: str, limit: int) -> list[str]:
        ids = [c for c in channels.split(",") if c]
        unknown = [c for c in ids if c not in lab.BY_ID]
        if unknown:
            raise HTTPException(422, f"unknown channels: {unknown[:5]}")
        if not ids or len(ids) > limit:
            raise HTTPException(422, f"choose 1..{limit} channels")
        return ids

    @app.get("/api/v1/recordings/{rec_id}/lab")
    def lab_meta(rec_id: str):
        rec, col = lab_columns(rec_id)
        store = need_recorder().store
        recorded = [{"type": e["type"], "t": e["elapsed_s"], "t_end": e["elapsed_s"], "label": f"{e['type'].replace('_', ' ')} {e['detail'] or ''}".strip(),
                     "rule": "logged by the receiver while recording", "confidence": "certain", "source": "recorded"}
                    for e in rec["events"] if e["elapsed_s"] is not None]
        auto = [dict(e, source="auto") for e in lab.detect_events(col)]
        return {
            "recording_id": rec_id,
            "title": rec["title"],
            "started_utc": rec["started_utc"],
            "mode": rec["mode"],
            "status": rec["status"],
            "duration_s": col.el[-1] if len(col) else 0.0,
            "samples": len(col),
            "channels": lab.channel_meta(col),
            "events": sorted(recorded + auto, key=lambda e: e["t"])[:2000],
            "annotations": store.annotations(rec_id),
        }

    @app.get("/api/v1/recordings/{rec_id}/lab/series")
    def lab_series(rec_id: str, channels: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to"),
                   points: int = Query(1200, ge=10, le=3000)):
        _, col = lab_columns(rec_id)
        a, b = lab_range(col, t_from, t_to)
        return lab.series(col, lab_ids(channels, 8), a, max(b, a + 1e-3), points)

    @app.get("/api/v1/recordings/{rec_id}/lab/summary")
    def lab_summary(rec_id: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to")):
        _, col = lab_columns(rec_id)
        return lab.summary(col, *lab_range(col, t_from, t_to))

    @app.get("/api/v1/recordings/{rec_id}/lab/gg")
    def lab_gg(rec_id: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to")):
        _, col = lab_columns(rec_id)
        return lab.gg(col, *lab_range(col, t_from, t_to))

    @app.get("/api/v1/recordings/{rec_id}/lab/route")
    def lab_route(rec_id: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to")):
        _, col = lab_columns(rec_id)
        return lab.route(col, *lab_range(col, t_from, t_to))

    @app.get("/api/v1/recordings/{rec_id}/lab/power")
    def lab_power(rec_id: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to"),
                  bin_rpm: int = Query(250, alias="bin", ge=50, le=2000)):
        _, col = lab_columns(rec_id)
        return lab.power_curve(col, *lab_range(col, t_from, t_to), bin_rpm=bin_rpm)

    @app.get("/api/v1/recordings/{rec_id}/lab/export.csv")
    def lab_export(rec_id: str, channels: str, t_from: Optional[float] = Query(None, alias="from"), t_to: Optional[float] = Query(None, alias="to")):
        _, col = lab_columns(rec_id)
        ids = lab_ids(channels, len(lab.CHANNELS))
        a, b = lab_range(col, t_from, t_to)
        name = f"pitwall-{rec_id}-{a:.0f}-{b:.0f}s.csv"
        return StreamingResponse(lab.export_rows(col, ids, a, b), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"'})

    # ------------------------------------------------------- performance runs (M5)
    def runs_for(rec: dict) -> list[dict]:
        store = need_recorder().store
        cached = store.cached_runs(rec["id"], perf.DETECTOR_VERSION, rec["samples"])
        if cached is not None:
            return cached
        _, col = lab_columns(rec["id"])
        runs = [r.as_dict() for r in perf.detect_runs(lab.perf_samples(col))]
        if rec["status"] != "recording":  # a growing recording is not cached
            store.store_runs(rec["id"], perf.DETECTOR_VERSION, rec["samples"], runs)
        return runs

    @app.get("/api/v1/recordings/{rec_id}/runs")
    def recording_runs(rec_id: str):
        rec = need_recorder().store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        return {"recording_id": rec_id, "detector_version": perf.DETECTOR_VERSION, "runs": runs_for(rec)}

    @app.get("/api/v1/runs")
    def all_runs(kind: Optional[str] = None, valid_only: bool = False):
        """Runs found in every saved recording, with the recording's labels next to them
        (the game does not send tuning; car/setup labels are the user's)."""
        store = need_recorder().store
        out = []
        for rec in store.list():
            if rec["samples"] == 0:
                continue
            for i, r in enumerate(runs_for(rec)):
                if (kind and r["kind"] != kind) or (valid_only and not r["valid"]):
                    continue
                out.append(r | {"recording_id": rec["id"], "run_index": i, "recording_title": rec["title"], "started_utc": rec["started_utc"],
                                "car_label": rec["car_label"], "setup_label": rec["setup_label"], "mode": rec["mode"], "recording_status": rec["status"]})
        return {"detector_version": perf.DETECTOR_VERSION, "kinds": perf.KINDS, "runs": out}

    @app.get("/api/v1/recordings/{rec_id}/runs/{index}/trace")
    def run_trace(rec_id: str, index: int, pad: float = Query(1.0, ge=0, le=5)):
        """Full-resolution samples of one run, time relative to its interpolated start."""
        rec = need_recorder().store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        runs = runs_for(rec)
        if not 0 <= index < len(runs):
            raise HTTPException(404, "run not found")
        r = runs[index]
        _, col = lab_columns(rec_id)
        lo, hi = col.index_range(r["t_start"] - pad, r["t_end"] + pad)
        ch = {c: col.ch[c] for c in ("speed", "throttle", "brake", "g_long")}
        nan_none = lambda v: None if v != v else round(v, 4)  # noqa: E731
        return {"run": r, "t": [round(col.el[k] - r["t_start"], 4) for k in range(lo, hi)],
                **{c: [nan_none(a[k]) for k in range(lo, hi)] for c, a in ch.items()}}

    # ------------------------------------------------------- car names (M6)
    def need_catalog() -> CarCatalog:
        if catalog is None:
            raise HTTPException(503, "the car catalog is not available in this instance")
        return catalog

    @app.get("/api/v1/cars")
    def cars():
        cat = need_catalog()
        recs = recorder.store.list(limit=1000) if recorder is not None else []
        return {
            "catalog": cat.list(),
            "seen": seen_cars(recs),
            "note": "Names are yours or imported by you; Pitwall ships no built-in list. Live values always come from telemetry.",
        }

    @app.put("/api/v1/cars/{ordinal}")
    def put_car(ordinal: int, req: CarEntry):
        try:
            return need_catalog().set_user({"ordinal": ordinal, **req.model_dump()})
        except CatalogError as e:
            raise HTTPException(422, str(e)) from None

    @app.delete("/api/v1/cars/{ordinal}")
    def delete_car(ordinal: int):
        if not need_catalog().delete(ordinal):
            raise HTTPException(404, "no name stored for this car")
        return {"deleted": ordinal}

    @app.post("/api/v1/cars/import")
    async def import_cars(request: Request):
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(413, "the file is larger than 2 MB")
        try:
            data = json.loads(body)
        except ValueError:
            raise HTTPException(422, "the file is not valid JSON") from None
        try:
            return need_catalog().import_file(data)
        except CatalogError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/v1/cars/export")
    def export_cars():
        doc = dumps_export(need_catalog().export())
        return PlainTextResponse(doc, media_type="application/json", headers={"Content-Disposition": 'attachment; filename="pitwall-car-names.json"'})

    @app.get("/api/v1/paths")
    def data_paths():
        """Where this instance keeps logs, recordings, captures and the car catalog."""
        return paths or {}

    @app.get("/api/v1/perf")
    def perf_status():
        return live_perf.status() | {"kinds": perf.KINDS}

    @app.post("/api/v1/perf/arm")
    def perf_arm(req: ArmRequest):
        try:
            return live_perf.arm(req.kind)
        except ValueError:
            raise HTTPException(422, f"kind must be one of {list(perf.KINDS)}") from None

    @app.post("/api/v1/perf/disarm")
    def perf_disarm():
        return live_perf.disarm()

    @app.post("/api/v1/recordings/{rec_id}/annotations", status_code=201)
    def add_annotation(rec_id: str, req: AnnotationRequest):
        text = req.text.strip()[:500]
        if not text:
            raise HTTPException(422, "the note is empty")
        import datetime as _dt
        aid = need_recorder().store.add_annotation(rec_id, max(0.0, req.elapsed_s), text,
                                                   _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))
        if aid is None:
            raise HTTPException(404, "recording not found")
        return {"id": aid, "elapsed_s": max(0.0, req.elapsed_s), "text": text}

    @app.delete("/api/v1/recordings/{rec_id}/annotations/{ann_id}")
    def delete_annotation(rec_id: str, ann_id: int):
        if not need_recorder().store.delete_annotation(rec_id, ann_id):
            raise HTTPException(404, "note not found")
        return {"deleted": ann_id}

    @app.get("/api/v1/recordings/{rec_id}/overview")
    def recording_overview(rec_id: str, points: int = Query(600, ge=10, le=4000)):
        """Speed envelope for the whole recording: per time bucket the min and max of
        speed (peaks are preserved). Buckets without samples are null (gaps stay visible)."""
        store = need_recorder().store
        rec = store.get(rec_id)
        if rec is None:
            raise HTTPException(404, "recording not found")
        duration = rec["duration_s"] or (rec["chunks"][-1]["t_end_s"] if rec["chunks"] else 0.0) or 0.0
        # never more buckets than ~half the samples, or a short recording looks dotted
        points = max(10, min(points, rec["samples"] // 2))
        width = max(duration / points, 1e-6)
        lo: list = [None] * points
        hi: list = [None] * points
        n = 0
        for line in store.iter_lines(rec_id):
            res = dec.decode(decode_datagram(line))
            if not res.ok:
                continue
            _, derived, _ = dec.normalize(res.raw)
            v = derived["speed_kmh"]
            if v is None:
                continue
            b = min(points - 1, int(line["el"] / width))
            lo[b] = v if lo[b] is None else min(lo[b], v)
            hi[b] = v if hi[b] is None else max(hi[b], v)
            n += 1
        return {"recording_id": rec_id, "duration_s": duration, "bucket_s": width, "speed_kmh_min": lo, "speed_kmh_max": hi, "samples_read": n}

    # ------------------------------------------------------- diagnostic capture
    def need_capture() -> DiagnosticCapture:
        if capture is None:
            raise HTTPException(503, "diagnostic capture is not available in this instance")
        return capture

    @app.get("/api/v1/diagnostics/capture")
    def capture_status():
        c = need_capture()
        return {"capture": c.status(), "captures": c.list()}

    @app.post("/api/v1/diagnostics/capture")
    def capture_start(req: CaptureRequest):
        c = need_capture()
        try:
            return c.start(req.seconds, req.mask_sources)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except RuntimeError as e:
            return JSONResponse({"error": "capture_busy", "state": str(e)}, status_code=409)

    @app.post("/api/v1/diagnostics/capture/stop")
    def capture_stop():
        return need_capture().stop()

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
        opts = {"raw": False, "force": True, "flow": False, "acked": 0}
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
                    elif msg.get("type") == "flow":
                        opts["flow"] = client.flow_control = bool(msg.get("ack"))
                    elif msg.get("type") == "ack" and isinstance(msg.get("n"), int):
                        opts["acked"] = max(opts["acked"], msg["n"])
                        signal.notify()

        reader_task = asyncio.create_task(reader())
        try:
            await ws.send_text(
                json.dumps({
                    "type": "hello", "api_version": API_VERSION, "schema_version": schema.SCHEMA_VERSION, "app_version": APP_VERSION,
                    "ui_rate_hz": cfg.ui_rate_hz, "server_instance_id": hub.server_instance_id,
                    "ui_build_id": (build.ui() or {}).get("ui_build_id"), "build_id": build.as_dict()["build_id"],
                    "flow": {"window": FLOW_WINDOW, "ack_every": FLOW_ACK_EVERY},
                })
            )
            sent_msgs = 1  # the hello counts; the client counts every message it receives
            blocked = False
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
                if opts["flow"] and sent_msgs - opts["acked"] >= FLOW_WINDOW:
                    if not blocked:
                        blocked = True
                        client.flow_pauses += 1
                    await signal.wait(0.05)  # an ack (or a new frame) wakes us
                    continue
                now = time.monotonic()
                since = now - last_send
                new = hub.publish_seq != last_seq
                if (new and since >= period * 0.9) or opts["force"] or since >= 0.5:
                    want_raw = opts["raw"] and now - last_raw >= raw_period
                    seq, msg = hub.message(include_raw=want_raw)
                    if last_seq >= 0 and seq > last_seq + 1:
                        if blocked:
                            client.flow_withheld_frames += seq - last_seq - 1
                        else:
                            client.ui_rate_limited_frames += seq - last_seq - 1
                    blocked = False
                    t_send = time.perf_counter()
                    try:
                        await asyncio.wait_for(ws.send_text(msg), timeout=2.0)
                    except asyncio.TimeoutError:
                        stream_stats["ws_send_timeouts"] += 1
                        raise
                    dt_ms = (time.perf_counter() - t_send) * 1000
                    client.send_ms_max = max(client.send_ms_max, round(dt_ms, 2))
                    if dt_ms > 250:
                        client.slow_sends += 1
                    client.frames_sent += 1
                    sent_msgs += 1
                    opts["force"] = False
                    last_seq, last_send = seq, now
                    if want_raw:
                        last_raw = now
                    continue
                # Wait for the next frame, or until the pending frame becomes due.
                timeout = max(0.002, (period - since) if new else (0.5 - since))
                await signal.wait(timeout)
        except WebSocketDisconnect:
            pass
        except (asyncio.TimeoutError, RuntimeError, ConnectionError) as e:
            stream_stats["ws_errors"] += 1
            log.info("event=ui_stream_error client=%s %s: %s", client.peer, type(e).__name__, e)
        finally:
            stream_stats["ws_closed"] += 1
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
