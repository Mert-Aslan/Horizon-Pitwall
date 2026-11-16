"""Comparable screenshots and browser performance numbers for the Pitwall UI.

Run inside tools/isolated_server.py (no --feed: this script feeds data itself so it can
stop the feed for the stale shot):

    python tools/isolated_server.py --web-port 18100 --udp-port 15620 [--dist <bundle>] -- \
        python tools/ui_capture.py --url http://127.0.0.1:18100/ --udp-port 15620 --label after \
        --shots docs/screenshots/compare --perf 30 --json out.json

Shots: six viewports opened at the same moment on the same deterministic synthetic feed
(CarOrdinal -2, live mode), captured after 22 s; then the feed stops and a 1920x1080
stale shot is taken. Perf (one 1920x1080 page, 60 Hz feed, after 5 s warm-up):
  * React commits: count and duration (needs the profiling bundle, npm run build:profile;
    otherwise reported as unavailable),
  * requestAnimationFrame callback time (wrapped via an init script; covers the trace
    canvas and the store's render batching),
  * long tasks (>50 ms), CDP Performance metrics deltas, JS heap (CDP JSHeapUsedSize) every 5 s.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp125x

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_server import Feeder  # noqa: E402

VIEWPORTS = [(1920, 1080), (2560, 1440), (1366, 768), (768, 1024), (390, 844), (960, 540)]

INIT = """
window.__raf = { n: 0, total: 0, list: [] };
const __origRaf = window.requestAnimationFrame.bind(window);
window.requestAnimationFrame = (cb) => __origRaf((t) => {
  const s = performance.now();
  try { cb(t); } finally {
    const d = performance.now() - s, r = window.__raf;
    r.n++; r.total += d; if (r.list.length < 200000) r.list.push(d);
  }
});
window.__lt = [];
try { new PerformanceObserver((l) => { for (const e of l.getEntries()) window.__lt.push(e.duration); }).observe({ type: 'longtask', buffered: true }); } catch (e) {}
"""


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3) if xs else None


def shots(p, url: str, udp: int, out: Path, label: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    browser = p.chromium.launch(headless=True)
    feeder = Feeder(udp, 6.0)
    feeder.start()
    time.sleep(1.0)
    pages = []
    for w, h in VIEWPORTS:
        pg = browser.new_page(viewport={"width": w, "height": h})
        pg.goto(url)
        pages.append(((w, h), pg))
    for _, pg in pages:
        pg.wait_for_selector(".speed-num", timeout=15_000)
    time.sleep(22)
    for (w, h), pg in pages:
        pg.screenshot(path=str(out / f"{label}-live-{w}x{h}.png"))
        sw = pg.evaluate("document.documentElement.scrollWidth")
        print(f"[shot] {label} {w}x{h} scrollWidth={sw}{'  OVERFLOW' if sw > w else ''}")
    feeder.stop_ev.set()
    feeder.join(2)
    time.sleep(2.5)
    pages[0][1].screenshot(path=str(out / f"{label}-stale-1920x1080.png"))
    browser.close()


def perf(p, url: str, udp: int, seconds: float) -> dict:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
    ctx.add_init_script(INIT)
    page = ctx.new_page()
    cdp = ctx.new_cdp_session(page)
    cdp.send("Performance.enable")
    feeder = Feeder(udp, 6.0)
    feeder.start()
    page.goto(url)
    page.wait_for_selector(".speed-num", timeout=15_000)
    time.sleep(5)
    page.evaluate("() => { window.__raf = { n: 0, total: 0, list: [] }; window.__lt = []; window.__pitwallCommits = []; }")
    m0 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    heap = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < seconds:
        mm = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
        heap.append(mm.get("JSHeapUsedSize", 0) / 1048576)
        time.sleep(5)
    m1 = {m["name"]: m["value"] for m in cdp.send("Performance.getMetrics")["metrics"]}
    data = page.evaluate("() => ({ raf: window.__raf, lt: window.__lt, commits: window.__pitwallCommits || [] })")
    feeder.stop_ev.set()
    feeder.join(2)
    browser.close()
    raf = data["raf"]["list"]
    commits = data["commits"]
    span = seconds

    def d(k):
        return round((m1.get(k, 0) - m0.get(k, 0)) * 1000 / span, 2)  # ms per second

    return {
        "seconds": span,
        "react_commits": None
        if not commits
        else {"per_s": round(len(commits) / span, 1), "mean_ms": round(statistics.mean(commits), 3), "p95_ms": pct(commits, 0.95), "max_ms": round(max(commits), 3)},
        "raf": {"per_s": round(len(raf) / span, 1), "ms_per_s": round(sum(raf) / span, 2), "p95_ms": pct(raf, 0.95), "max_ms": round(max(raf), 3) if raf else None},
        "long_tasks": {"count": len(data["lt"]), "max_ms": round(max(data["lt"]), 1) if data["lt"] else None},
        "cdp_ms_per_s": {"script": d("ScriptDuration"), "task": d("TaskDuration"), "layout": d("LayoutDuration"), "style": d("RecalcStyleDuration")},
        "js_heap_mib": [round(h, 2) if h is not None else None for h in heap],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--udp-port", type=int, required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--shots", help="output dir for screenshots")
    ap.add_argument("--perf", type=float, default=0)
    ap.add_argument("--json")
    a = ap.parse_args()
    if a.udp_port == 5607:
        raise SystemExit("refusing the game port")
    result = {"label": a.label}
    with sync_playwright() as p:
        if a.shots:
            shots(p, a.url, a.udp_port, Path(a.shots), a.label)
            time.sleep(2)
        if a.perf:
            result["perf"] = perf(p, a.url, a.udp_port, a.perf)
            print(json.dumps(result["perf"], indent=1))
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
