"""Local load / latency measurement against a running Pitwall instance.

    python tools/loadtest.py --udp-port 15612 --web-port 18092 --hz 60 --seconds 120 --clients 2 [--pid 1234]

What is measured (and what is not):
  * latency = WebSocket client receive time - frame.received_at. Both are wall-clock
    readings on THIS machine (same clock), so the difference is server receive ->
    client receive including JSON serialisation and the 30 Hz broadcast tick. It does
    NOT include browser rendering (requestAnimationFrame adds up to one display frame).
  * frames/s per client, skipped display frames (latest-wins), server CPU% and RSS via
    psutil when --pid is given.
Game FPS impact cannot be measured without the game and is not claimed.
"""

import argparse
import asyncio
import json
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import websockets  # noqa: E402

from backend.telemetry.synth import build_packet, demo_values  # noqa: E402


async def sender(port: int, hz: float, seconds: float) -> int:
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    n = 0
    nxt = t0
    while loop.time() - t0 < seconds:
        vals = demo_values(loop.time() - t0 + 1)
        vals["CarOrdinal"] = -2
        vals["TimestampMS"] = int((loop.time() - t0) * 1000 * 1000 / 1000) + n  # strictly increasing
        tx.sendto(build_packet(vals), ("127.0.0.1", port))
        n += 1
        nxt += 1 / hz
        await asyncio.sleep(max(0.0, nxt - loop.time()))
    return n


async def client(web_port: int, seconds: float, out: dict) -> None:
    lat: list[float] = []
    frames = 0
    seqs = set()
    async with websockets.connect(f"ws://127.0.0.1:{web_port}/api/v1/stream", max_size=2**22) as ws:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            try:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=2))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") != "frame" or not msg.get("frame"):
                continue
            f = msg["frame"]
            if f["sequence"] in seqs:
                continue  # heartbeat repeating the same frame
            seqs.add(f["sequence"])
            frames += 1
            lat.append(time.time() * 1000 - f["received_at"])
    out["frames"] = frames
    out["latency_ms"] = lat


async def sampler(pid: int, seconds: float, out: dict) -> None:
    import psutil

    p = psutil.Process(pid)
    p.cpu_percent(None)
    cpu, rss = [], []
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        await asyncio.sleep(1.0)
        cpu.append(p.cpu_percent(None))
        rss.append(p.memory_info().rss / 2**20)
    out.update(cpu=cpu, rss=rss)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else float("nan")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp-port", type=int, required=True)
    ap.add_argument("--web-port", type=int, required=True)
    ap.add_argument("--hz", type=float, default=60)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--clients", type=int, default=2)
    ap.add_argument("--pid", type=int)
    a = ap.parse_args()
    if a.udp_port == 5607:
        raise SystemExit("refusing to load-test the default game port 5607")
    outs = [dict() for _ in range(a.clients)]
    samp: dict = {}
    tasks = [client(a.web_port, a.seconds, o) for o in outs]
    if a.pid:
        tasks.append(sampler(a.pid, a.seconds, samp))
    await asyncio.sleep(0)
    sent, *_ = await asyncio.gather(sender(a.udp_port, a.hz, a.seconds), *tasks)
    print(f"input: {a.hz} Hz for {a.seconds:.0f} s, packets sent {sent}")
    for i, o in enumerate(outs):
        lat = o["latency_ms"]
        print(
            f"client {i}: frames {o['frames']} ({o['frames'] / a.seconds:.1f}/s), latency ms p50 {statistics.median(lat):.1f} "
            f"p95 {pct(lat, 0.95):.1f} p99 {pct(lat, 0.99):.1f} max {max(lat):.1f}"
        )
    if samp:
        print(
            f"server: CPU% mean {statistics.mean(samp['cpu']):.1f} max {max(samp['cpu']):.1f} (100 = one core); "
            f"RSS MiB start {samp['rss'][0]:.1f} end {samp['rss'][-1]:.1f} max {max(samp['rss']):.1f}"
        )


if __name__ == "__main__":
    asyncio.run(main())
