"""Headless browser smoke test of the Pitwall UI (Python Playwright, webapp-testing style).

Self-hosting: starts its own isolated Pitwall (tools/isolated_server.py: PID + process
tree tracking, protected ports 5607/8080 refused), restarts it once for the reconnect
check, and verifies the test ports are free at the end.

    python tools/ui_smoke.py [--web-port 18100] [--udp-port 15620] [--out docs/screenshots/smoke]

Covers: demo labelling and live values, Diagnostics inspector + filter, Focus view and
its persistence, chart keyboard inspection, pause / back to live while data keeps
arriving, demo stop feedback, waiting -> receiving -> stale with dimmed values, server
restart and reconnect, phone and 200 %-zoom widths, console/page errors.
Exits non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp125x

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_server import Feeder, IsolatedPitwall  # noqa: E402

failures: list[str] = []


def check(cond: bool, label: str) -> None:
    print(("PASS " if cond else "FAIL ") + label, flush=True)
    if not cond:
        failures.append(label)


def wait_until(fn, timeout=8.0, step=0.1) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001 - element may not exist yet
            pass
        time.sleep(step)
    return False


def canvas_hash(page) -> str:
    return page.evaluate("() => { const c = document.querySelector('.trace-canvas'); return c ? c.toDataURL().length + ':' + c.toDataURL().slice(-64) : ''; }")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-port", type=int, default=18100)
    ap.add_argument("--udp-port", type=int, default=15620)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    out = Path(a.out) if a.out else None
    if out:
        out.mkdir(parents=True, exist_ok=True)
    url = f"http://127.0.0.1:{a.web_port}/"
    errors: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))

        # ---------------------------------------------------------------- demo
        with IsolatedPitwall(a.web_port, a.udp_port, demo=True):
            page.goto(url)
            page.wait_for_load_state("networkidle")
            page.wait_for_selector(".speed-num", timeout=10_000)
            page.wait_for_timeout(1500)
            check(page.locator(".mode-badge").inner_text() == "Demo", "demo badge visible")
            check(page.locator(".banner-demo").count() == 1, "demo banner visible")
            check(page.locator(".conn").inner_text().strip() == "Receiving", "connection state Receiving")
            speed = page.locator(".speed-num").inner_text()
            check(speed.isdigit(), f"speed renders a number ({speed!r})")
            check(page.locator(".gear-num").inner_text() != "—", "gear renders")
            check(page.locator(".car-name").inner_text() == "Demo car", "demo car labelled")
            box = page.locator(".speed-num").bounding_box()
            page.wait_for_timeout(700)
            box2 = page.locator(".speed-num").bounding_box()
            check(abs(box["x"] + box["width"] - box2["x"] - box2["width"]) < 1, "speed right edge stays put while value changes")
            if out:
                page.screenshot(path=str(out / "live-1920.png"))

            # chart: keyboard inspection
            canvas = page.locator(".trace-canvas")
            canvas.focus()
            page.keyboard.press("ArrowLeft")
            page.keyboard.press("Shift+ArrowLeft")
            check(wait_until(lambda: page.locator(".ro-time dd").inner_text().strip() in ("−1.1 s", "-1.1 s")), "arrow keys move the chart cursor (1.1 s ago)")
            vt = canvas.get_attribute("aria-valuetext") or ""
            check("speed" in vt and "km/h" in vt, f"cursor values exposed as text ({vt[:60]!r})")
            # pause: picture frozen while data keeps arriving
            page.keyboard.press("Space")
            check(wait_until(lambda: page.locator(".traces.is-paused").count() == 1), "Space pauses the chart")
            seq0 = page.evaluate("fetch('/api/v1/telemetry/latest').then(r => r.json()).then(j => j.status.sequence)")
            h0 = canvas_hash(page)
            page.wait_for_timeout(1200)
            seq1 = page.evaluate("fetch('/api/v1/telemetry/latest').then(r => r.json()).then(j => j.status.sequence)")
            check(seq1 > seq0, f"data keeps arriving while paused ({seq0} -> {seq1})")
            check(canvas_hash(page) == h0, "paused chart does not move")
            check(page.locator(".speed-num").inner_text() != "" and page.locator(".conn").inner_text().strip() == "Receiving", "live panels keep updating while chart is paused")
            if out:
                page.screenshot(path=str(out / "chart-paused-1920.png"))
            page.get_by_role("button", name="Back to live").click()
            check(wait_until(lambda: page.locator(".traces.is-paused").count() == 0), "Back to live resumes")
            h1 = canvas_hash(page)
            page.wait_for_timeout(500)
            check(canvas_hash(page) != h1, "live chart redraws after resume")
            page.keyboard.press("Escape")

            # focus view + persistence
            page.get_by_role("button", name="Focus view").click()
            check(wait_until(lambda: page.locator(".live.is-focus").count() == 1), "Focus view on")
            check(page.locator(".traces").count() == 0 and page.locator(".output").count() == 0, "Focus hides chart and car output")
            check(page.locator(".pedal-track").count() == 2 and page.locator(".chassis-strip").count() == 1, "Focus keeps pedals and compact tyres")
            xs = sorted(round(page.locator(f".chassis-strip .wheel-{k}").bounding_box()["x"]) for k in ("fl", "fr", "rl", "rr"))
            check(len(set(xs)) == 4, f"Focus shows four separate tyre cells ({xs})")
            if out:
                page.screenshot(path=str(out / "focus-1920.png"))
            page.reload()
            page.wait_for_selector(".speed-num")
            check(page.locator(".live.is-focus").count() == 1, "Focus preference survives reload")
            page.get_by_role("button", name="Focus view").click()
            check(wait_until(lambda: page.locator(".live.is-focus").count() == 0), "Focus view off")

            # clutch/handbrake: user-controlled, not automatic
            check(page.locator(".aux-inputs").count() == 1, "clutch/handbrake compact by default")
            page.get_by_role("button", name="Expand clutch & handbrake").click()
            check(wait_until(lambda: page.locator(".pedal-track").count() == 4), "expanded shows four pedal bars")
            page.get_by_role("button", name="Compact clutch & handbrake").click()

            # diagnostics
            page.get_by_role("button", name="Diagnostics").click()
            page.wait_for_selector("table tbody tr", timeout=5_000)
            page.wait_for_timeout(800)
            check(page.locator("table tbody tr").count() == 88, "inspector lists 88 fields")
            page.get_by_label("Filter fields").fill("TireTemp")
            check(wait_until(lambda: page.locator("table tbody tr").count() == 4), "filter narrows to 4 tyre temps")
            page.get_by_role("button", name="Live").click()

            # phone + 200 % zoom widths
            for w, h in ((390, 844), (960, 540)):
                page.set_viewport_size({"width": w, "height": h})
                page.wait_for_timeout(600)
                sw = page.evaluate("document.documentElement.scrollWidth")
                check(sw <= w, f"no horizontal scroll at {w} px ({sw})")
                if out:
                    page.screenshot(path=str(out / f"live-{w}.png"))
            check(page.locator(".pedal-track").first.is_visible(), "throttle visible at 960 px")
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(300)
            th = page.locator(".pedal-track").first.bounding_box()
            check(th is not None and th["y"] + th["height"] <= 844, "throttle within first phone screen")
            page.set_viewport_size({"width": 1920, "height": 1080})

            # demo stop with feedback -> waiting
            page.locator(".banner-demo").get_by_role("button", name="Stop demo").click()
            check(wait_until(lambda: page.locator("#empty-title").inner_text() == "Waiting for telemetry"), "stopping demo returns to the waiting screen")

            # live feed -> receiving -> stale (dimmed values)
            feeder = Feeder(a.udp_port, 6.0)
            feeder.start()
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Receiving"), "synthetic live feed is received")
            check(page.locator(".car-name").inner_text() == "Car #-2", "unknown car shown by id")
            feeder.stop_ev.set()
            feeder.join(2)
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Data stale", timeout=4), "silence turns into Data stale")
            check(page.locator(".live.is-dim").count() == 1, "stale values are dimmed")
            color = page.evaluate("getComputedStyle(document.querySelector('.speed-num')).color")
            check(color == "rgb(133, 141, 149)", f"stale value colour is text-3 ({color})")
            page.set_viewport_size({"width": 1400, "height": 900})
            page.wait_for_timeout(400)
            cw = page.evaluate("document.querySelector('.trace-canvas').width")
            check(cw > 0 and abs(cw - page.evaluate("document.querySelector('.trace-canvas').getBoundingClientRect().width * devicePixelRatio")) < 3, "stale chart redraws at the new size")
            page.set_viewport_size({"width": 1920, "height": 1080})
            if out:
                page.screenshot(path=str(out / "stale-1920.png"))

        # ---------------------------------------------------------------- server gone
        check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Server unreachable", timeout=8), "server stop shows Server unreachable")
        check(page.locator(".banner-danger").count() >= 1, "lost-connection banner shown")
        with IsolatedPitwall(a.web_port, a.udp_port):
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Waiting for data", timeout=15), "page reconnects after server restart")

        real_errors = [e for e in errors if "WebSocket" not in e and "ERR_CONNECTION_REFUSED" not in e]
        check(not real_errors, f"no console/page errors apart from the deliberate server stop ({real_errors[:3]})")
        browser.close()

    print(f"\n{'OK' if not failures else 'FAILED'}: {len(failures)} failed check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
