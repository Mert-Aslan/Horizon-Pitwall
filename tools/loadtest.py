"""Local load / latency measurement against a running (isolated) Pitwall instance.

    python tools/loadtest.py --udp-port 15612 --web-port 18092 --hz 60 --seconds 120 \
        --clients 2 [--slow-clients 1] [--reconnect-every 20] [--record] [--warmup 10] \
        [--pid 1234] [--json out.json]

Run it against an instance started by tools/isolated_server.py (never the game port 5607;
the tool refuses it). Nothing here is sent to the user's app.

What is measured (and what is not):
  * transport latency = WebSocket client receive time - frame.received_at. Both are wall
    clock readings on THIS machine, so the difference is server receive -> client receive
    including JSON serialisation and the UI rate limiter. It does NOT include browser
    rendering (display latency adds up to one display frame; measured separately by
    tools/ui_capture.py).
  * frames/s per client; a slow client reads with a 200 ms pause per message (shows
    whether the server isolates it); reconnecting clients drop and reopen periodically.
  * server CPU% and RSS per second via psutil when --pid is given, reported after the
    warm-up boundary (RSS growth during warm-up is buffer fill, not a leak signal).
  * --record starts a recording for the run and reports the recorder's queue peak and
    dropped samples.
Memory of this tool is bounded: latencies are kept in a reservoir of at most 50 000
values per client; sequence de-duplication keeps only the last id.
Game FPS impact cannot be measured without the game and is not claimed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import socket
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402

from backend.telemetry.synth import build_packet, demo_values  # noqa: E402

RESERVOIR = 50_000


class Reservoir:
    def __init__(self, k: int = RESERVOIR) -> None:
        self.k, self.n, self.xs = k, 0, []

    def add(self, x: float) -> None:
        self.n += 1
        if len(self.xs) < self.k:
            self.xs.append(x)
        else:
            j = random.randrange(self.n)
            if j < self.k:
                self.xs[j] = x


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 2) if xs else None


async def sender(port: int, hz: float, seconds: float) -> int:
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    n = 0
    nxt = t0
    while loop.time() - t0 < seconds:
        vals = demo_values(loop.time() - t0 + 1)
        vals["CarOrdinal"] = -2
        vals["TimestampMS"] = int((loop.time() - t0) * 1000) + n  # strictly increasing
        tx.sendto(build_packet(vals), ("127.0.0.1", port))
        n += 1
        nxt += 1 / hz
        await asyncio.sleep(max(0.0, nxt - loop.time()))
    return n


async def client(web_port: int, seconds: float, warmup: float, out: dict, slow: bool = False, reconnect_every: float = 0, ack: bool = True) -> None:
    lat = Reservoir()
    frames = frames_after_warmup = reconnects = 0
    last_key = None
    t_start = time.monotonic()
    end = t_start + seconds
    while time.monotonic() < end:
        try:
            async with websockets.connect(f"ws://127.0.0.1:{web_port}/api/v1/stream", max_size=2**22) as ws:
                conn_end = min(end, time.monotonic() + reconnect_every) if reconnect_every else end
                rx = 0
                ack_every = 0
                while time.monotonic() < conn_end:
                    try:
                        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
                    except asyncio.TimeoutError:
                        continue
                    rx += 1
                    if msg.get("type") == "hello" and msg.get("flow") and ack:
                        ack_every = msg["flow"]["ack_every"]  # behave like the browser client
                        await ws.send(json.dumps({"type": "flow", "ack": True}))
                    if ack_every and rx % ack_every == 0:
                        await ws.send(json.dumps({"type": "ack", "n": rx}))
                    if slow:
                        await asyncio.sleep(0.2)
                    f = msg.get("frame") if msg.get("type") == "frame" else None
                    if not f:
                        continue
                    key = (f.get("stream_segment_id"), f["sequence"])
                    if key == last_key:
                        continue  # heartbeat repeating the same frame
                    last_key = key
                    frames += 1
                    if time.monotonic() - t_start >= warmup:
                        frames_after_warmup += 1
                        lat.add(time.time() * 1000 - f["received_at"])
        except (OSError, websockets.exceptions.WebSocketException) as e:
            out.setdefault("errors", []).append(f"{type(e).__name__}: {e}"[:200])
            await asyncio.sleep(0.5)
        if reconnect_every and time.monotonic() < end:
            reconnects += 1
    measured = max(1e-9, seconds - warmup)
    out.update(
        kind=("slow" if slow else ("reconnecting" if reconnect_every else "normal")) + ("" if ack else ", no flow control"),
        frames=frames,
        frames_per_s=round(frames_after_warmup / measured, 2),
        reconnects=reconnects,
        latency_ms={"n": lat.n, "p50": pct(lat.xs, 0.5), "p95": pct(lat.xs, 0.95), "p99": pct(lat.xs, 0.99), "max": round(max(lat.xs), 2) if lat.xs else None},
    )


async def sampler(pid: int, seconds: float, out: dict) -> None:
    import psutil

    p = psutil.Process(pid)
    p.cpu_percent(None)
    series = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        await asyncio.sleep(1.0)
        series.append({"t": round(time.monotonic() - t0, 1), "cpu": p.cpu_percent(None), "rss_mib": round(p.memory_info().rss / 2**20, 2)})
    out["series"] = series


def api(web_port: int, path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{web_port}{path}", method=method, data=b"{}" if method == "POST" else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp-port", type=int, required=True)
    ap.add_argument("--web-port", type=int, required=True)
    ap.add_argument("--hz", type=float, default=60)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--warmup", type=float, default=10)
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--slow-clients", type=int, default=0)
    ap.add_argument("--reconnect-every", type=float, default=0, help="extra client that reconnects every N s (0 = none)")
    ap.add_argument("--record", action="store_true", help="record during the run (stopped at the end)")
    ap.add_argument("--no-ack", action="store_true", help="slow clients do not use flow control (0.2 behaviour)")
    ap.add_argument("--pid", type=int)
    ap.add_argument("--json", help="write the full result (incl. per-second CPU/RSS) to this file")
    a = ap.parse_args()
    if a.udp_port == 5607 or a.web_port == 8080:
        raise SystemExit("refusing to load-test the default game port 5607 / the user's app port 8080")
    if a.warmup >= a.seconds:
        raise SystemExit("--warmup must be shorter than --seconds")

    rec_id = None
    if a.record:
        rec_id = api(a.web_port, "/api/v1/recordings/start", "POST")["recording_id"]
    outs = [dict() for _ in range(a.clients)]
    slow_outs = [dict() for _ in range(a.slow_clients)]
    rc_out: dict = {}
    samp: dict = {}
    tasks = [client(a.web_port, a.seconds, a.warmup, o) for o in outs]
    tasks += [client(a.web_port, a.seconds, a.warmup, o, slow=True, ack=not a.no_ack) for o in slow_outs]
    if a.reconnect_every:
        tasks.append(client(a.web_port, a.seconds, a.warmup, rc_out, reconnect_every=a.reconnect_every))
    if a.pid:
        tasks.append(sampler(a.pid, a.seconds, samp))
    sent, *_ = await asyncio.gather(sender(a.udp_port, a.hz, a.seconds), *tasks)

    result: dict = {"input": {"hz": a.hz, "seconds": a.seconds, "warmup_s": a.warmup, "packets_sent": sent}, "clients": outs + slow_outs + ([rc_out] if rc_out else [])}
    if rec_id:
        stop = api(a.web_port, "/api/v1/recordings/stop", "POST")
        diag = api(a.web_port, "/api/v1/diagnostics")["recorder"]
        result["recording"] = {"id": rec_id, "samples": stop["samples"], "dropped": stop["dropped"], "queue_high_watermark": diag["queue_high_watermark"], "flush_ms_max": diag["flush_ms_max"]}
    if samp.get("series"):
        after = [s for s in samp["series"] if s["t"] > a.warmup] or samp["series"]
        result["server"] = {
            "cpu_pct_mean_after_warmup": round(statistics.mean(s["cpu"] for s in after), 1),
            "cpu_pct_max": max(s["cpu"] for s in samp["series"]),
            "rss_mib_start": samp["series"][0]["rss_mib"],
            "rss_mib_at_warmup_end": after[0]["rss_mib"],
            "rss_mib_end": samp["series"][-1]["rss_mib"],
            "rss_mib_max": max(s["rss_mib"] for s in samp["series"]),
            "series": samp["series"],
        }

    print(f"input: {a.hz} Hz for {a.seconds:.0f} s (warm-up {a.warmup:.0f} s excluded), packets sent {sent}")
    for i, o in enumerate(result["clients"]):
        lm = o.get("latency_ms", {})
        if not lm.get("n"):
            print(f"client {i} ({o.get('kind')}): no frames received after warm-up; errors: {o.get('errors', [])[:2]}")
            continue
        print(f"client {i} ({o['kind']}): {o['frames_per_s']} frames/s, transport latency ms p50 {lm['p50']} p95 {lm['p95']} p99 {lm['p99']} max {lm['max']}, reconnects {o['reconnects']}")
    if "recording" in result:
        r = result["recording"]
        print(f"recording: {r['samples']} samples, {r['dropped']} dropped, queue peak {r['queue_high_watermark']}, slowest chunk flush {r['flush_ms_max']} ms")
    if "server" in result:
        s = result["server"]
        print(f"server: CPU% mean {s['cpu_pct_mean_after_warmup']} (after warm-up) max {s['cpu_pct_max']} (100 = one core); RSS MiB start {s['rss_mib_start']} "
              f"warm-up end {s['rss_mib_at_warmup_end']} end {s['rss_mib_end']} max {s['rss_mib_max']}")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(f"written: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
