"""Browser smoke tests against an isolated Pitwall instance.

Run python tools/ui_smoke.py --help for ports and screenshot options.
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


WS_COUNTER = """(() => { const W = window.WebSocket; window.__ws = {made: 0, closed: 0};
  function Counted(u, p) { const s = new W(u, p); window.__ws.made++; s.addEventListener('close', () => window.__ws.closed++); return s; }
  Counted.prototype = W.prototype; Counted.OPEN = 1; Counted.CLOSED = 3; window.WebSocket = Counted; })();"""


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
        # Count WebSocket connections in the page.
        ctx.add_init_script(WS_COUNTER)
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
            check(page.get_by_text("Fuel", exact=True).count() == 0, "live has no fuel gauge")
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
            check(page.locator(".inspector tbody tr").count() == 87, "inspector lists supported fields")
            page.get_by_label("Filter fields").fill("Fuel")
            check(wait_until(lambda: page.locator(".inspector tbody tr").count() == 0), "fuel is excluded from the inspector")
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
            check(wait_until(lambda: page.locator(".car-name").inner_text() == "Car #-2"), "unknown car shown by id")

            # empty game packets (real FH6 outside a drive): not shown as measured zeros
            speed_before = page.locator(".speed-num").inner_text()
            feeder.empty = True
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "No driving data"), "empty packets show 'No driving data'")
            check(page.locator(".live.is-dim").count() == 1 and page.locator(".speed-num").inner_text() not in ("0", "—"), f"last real values stay, dimmed (speed {speed_before} -> {page.locator('.speed-num').inner_text()})")
            check("without driving data" in page.locator(".banners").inner_text(), "banner explains the empty packets")
            if out:
                page.screenshot(path=str(out / "no-driving-data-1920.png"))
            feeder.empty = False
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Receiving"), "real data again -> Receiving")

            # live performance run: arm, see the server's state, cancel
            perf = page.locator(".perf-run")
            check(perf.count() == 1, "Performance run control shown in Live")
            perf.locator("select").select_option("accel_0_100")
            perf.get_by_role("button", name="Arm").click()
            check(wait_until(lambda: "Armed" in perf.inner_text() or "Ready" in perf.inner_text()), f"arming shows the server state ({perf.inner_text()[:60]!r})")
            check("Stop the car" in perf.inner_text(), "arming explains how the 0-100 run starts")
            st = page.evaluate("fetch('/api/v1/perf').then(r => r.json()).then(j => j.state)")
            check(st in ("armed", "ready"), f"server reports the run as armed ({st})")
            perf.get_by_role("button", name="Cancel").click()
            check(wait_until(lambda: perf.get_by_role("button", name="Arm").count() == 1), "Cancel returns to Arm")

            # Suspend animation frames while telemetry continues.
            n_sock = page.evaluate("window.__ws.made")
            page.evaluate("""() => { window.__rafHeld = []; window.__rafOrig = window.requestAnimationFrame;
                window.requestAnimationFrame = (cb) => { window.__rafHeld.push(cb); return 0; }; }""")
            page.wait_for_timeout(6000)
            held = page.evaluate("window.__rafHeld.length")
            n_after = page.evaluate("window.__ws.made")
            check(n_after == n_sock, f"no reconnect while rAF is suspended and data arrives ({n_sock} -> {n_after} sockets, {held} rAF held)")
            page.evaluate("() => { window.requestAnimationFrame = window.__rafOrig; window.__rafHeld.forEach((cb) => cb(performance.now())); }")
            check(wait_until(lambda: page.locator(".conn").inner_text().strip() == "Receiving"), "page shows Receiving again when rAF resumes")

            # recording: the button mirrors the server, Sessions shows the result
            rec_btn = page.locator(".rec-btn")
            check(rec_btn.inner_text().strip().endswith("Record"), "Record button shown")
            rec_btn.click()
            check(wait_until(lambda: page.locator(".rec-btn-on").count() == 1), "Record turns into Stop once the server records")
            check(wait_until(lambda: "samples" in page.locator(".rec-meta").first.inner_text()), "recording shows elapsed time and samples")
            page.wait_for_timeout(2500)
            if out:
                page.screenshot(path=str(out / "recording-1920.png"))
            page.locator(".rec-btn").click()
            check(wait_until(lambda: page.locator(".rec-btn-on").count() == 0), "Stop returns to Record")
            page.get_by_role("button", name="Sessions").click()
            check(wait_until(lambda: page.locator(".session-row").count() >= 1), "Sessions lists the recording")
            check(wait_until(lambda: int(page.locator(".session-detail .kv div:has(dt:text('Samples saved')) dd").inner_text().replace(",", "").replace(".", "")) > 100), "detail shows saved samples")
            page.get_by_role("button", name="Check files").click()
            check(wait_until(lambda: "files verified" in page.locator(".session-detail").inner_text()), "integrity check passes in the UI")
            page.get_by_label("Car label").fill("Smoke car")
            page.get_by_role("button", name="Save labels").click()
            check(wait_until(lambda: "Saved." in page.locator(".session-detail").inner_text()), "labels saved")
            check(wait_until(lambda: "Smoke car" in page.locator(".session-row").first.inner_text()), "list shows the new label")
            check(page.locator(".overview-canvas").count() == 1, "speed overview drawn")

            # replay: client-side, does not touch the live stream
            seq_live0 = page.evaluate("fetch('/api/v1/telemetry/latest').then(r => r.json()).then(j => j.status.sequence)")
            page.locator(".session-replay").click()
            check(wait_until(lambda: page.locator(".replay-bar .mode-replay").is_visible()), "Replay opens with a Replay badge")
            check(wait_until(lambda: page.locator(".speed-num").count() == 1), "replay shows the cockpit panels")
            check(page.get_by_text("Fuel", exact=True).count() == 0, "replay has no fuel gauge")
            check(page.locator(".conn").inner_text().strip().startswith("Live:"), "top bar labels the live link, not the replay, as Receiving")
            check("before the playhead" in page.locator("#traces-title").inner_text(), "chart follows the playhead")
            rid = page.evaluate("fetch('/api/v1/recordings').then(r => r.json()).then(j => j.recordings[0].id)")
            first = page.evaluate(f"fetch('/api/v1/recordings/{rid}/frames?from=0&to=1').then(r => r.json()).then(j => j.rows[0].frame.derived.speed_kmh)")
            check(wait_until(lambda: page.locator(".speed-num").inner_text() == str(round(first))), f"value at position 0 matches the recording ({first:.1f} km/h)")
            page.locator(".timeline").focus()
            page.keyboard.press("End")
            check(wait_until(lambda: "End of the recording" in page.locator(".replay-note").inner_text()), "End jumps to the end")
            last_rows = page.evaluate(f"fetch('/api/v1/recordings/{rid}/frames?from=0&to=60').then(r => r.json()).then(j => j.rows.slice(-1)[0].frame.derived.speed_kmh)")
            check(wait_until(lambda: page.locator(".speed-num").inner_text() == str(round(last_rows))), f"value at the end matches the last sample ({last_rows:.1f} km/h)")
            page.keyboard.press("Home")
            page.get_by_role("button", name="2×").click()
            page.locator(".replay-play").click()
            t0 = page.locator(".replay-time").inner_text()
            page.wait_for_timeout(700)
            t1 = page.locator(".replay-time").inner_text()
            check(t1 != t0 and page.locator(".replay-play").inner_text() in ("Pause", "Play again"), f"playback advances at 2x ({t0} -> {t1})")
            seq_live1 = page.evaluate("fetch('/api/v1/telemetry/latest').then(r => r.json()).then(j => j.status.sequence)")
            mode = page.evaluate("fetch('/api/v1/health').then(r => r.json()).then(j => j.mode)")
            check(seq_live1 > seq_live0 and mode == "live", f"live stream keeps running during replay ({seq_live0} -> {seq_live1}, mode {mode})")
            if out:
                page.screenshot(path=str(out / "replay-1920.png"))
            page.get_by_role("button", name="Close replay").click()
            check(wait_until(lambda: page.locator(".session-row").count() >= 1 and page.locator(".replay-bar").count() == 0), "Close replay returns to Sessions")

            # runs & comparison view
            page.get_by_role("button", name="Runs & comparison").click()
            check(wait_until(lambda: page.locator(".runs-table").count() == 1 or "No " in page.locator(".runs").inner_text(), timeout=15), "Runs view lists runs or explains that there are none")
            if page.locator(".runs-table tbody tr").count() >= 2:
                page.locator(".runs-table tbody input[type=checkbox]").nth(0).check()
                page.locator(".runs-table tbody input[type=checkbox]").nth(1).check()
                check(wait_until(lambda: page.locator(".runs-overlay").count() == 1), "two runs compared")
            page.get_by_role("button", name="Recordings").click()
            page.wait_for_selector(".session-row")

            # Garage: name a car id, see provenance, export, import, remove
            page.get_by_role("button", name="Garage").click()
            page.get_by_role("heading", name="Garage").wait_for()
            page.get_by_label("Name another car id").fill("4210")
            page.get_by_role("button", name="Add").click()
            page.get_by_label("Name", exact=True).fill("Smoke Hypercar")
            page.get_by_label("Year").fill("2024")
            page.get_by_role("button", name="Save name").click()
            check(wait_until(lambda: "2024 Smoke Hypercar" in page.locator(".garage-list").inner_text() and "your name" in page.locator(".garage-list").inner_text()), "named car listed with its provenance")
            exp = page.request.get(url.rstrip("/") + "/api/v1/cars/export")
            check(exp.status == 200 and exp.json()["cars"][0]["ordinal"] == 4210, "names export as a file")
            import json as _json
            import tempfile as _tf
            f = Path(_tf.gettempdir()) / "pitwall-smoke-cars.json"
            f.write_text(_json.dumps({"format": "pitwall-car-aliases", "format_version": 1, "game": "fh6", "source": "smoke list", "version": "1",
                                      "cars": [{"ordinal": 4210, "name": "Other"}, {"ordinal": 77, "name": "Seventy-seven"}]}), encoding="utf-8")
            page.locator(".file-btn input").set_input_files(str(f))
            check(wait_until(lambda: "1 added" in page.locator(".garage").inner_text() and "of your names kept" in page.locator(".garage").inner_text()),
                  "import adds new names and keeps the user's own")
            check("imported from smoke list" in page.locator(".garage-list").inner_text(), "imported name shows where it came from")
            f.unlink(missing_ok=True)
            if out:
                page.screenshot(path=str(out / "garage-1920.png"), full_page=True)
            row = page.locator(".garage-list > li", has_text="#4210")
            row.get_by_role("button", name="Edit").click()
            page.get_by_role("button", name="Remove name").click()
            check(wait_until(lambda: "Smoke Hypercar" not in page.locator(".garage-list").inner_text()), "name removed")
            page.get_by_role("button", name="Sessions").click()
            page.wait_for_selector(".session-row")

            # Telemetry Lab on the same recording
            page.evaluate("localStorage.setItem('pitwall.lab.channels', JSON.stringify(['fuel', 'speed', 'throttle', 'brake', 'steer']))")
            page.locator(".session-analyze").click()
            check(wait_until(lambda: page.locator(".lab-stats tbody tr").count() >= 1, timeout=10), "Analyze opens the Lab with exact statistics")
            check(page.get_by_role("checkbox", name="Fuel").count() == 0, "Lab has no fuel channel")
            check("fuel" not in page.get_by_role("link", name="Export CSV").get_attribute("href"), "CSV ignores saved fuel selections")
            check(page.locator(".lab-bar .mode-lab").is_visible() and page.locator(".conn").inner_text().strip().startswith("Live:"), "Lab badge shown; top bar describes the live link")
            api_max = page.evaluate(f"fetch('/api/v1/recordings/{rid}/lab/series?channels=speed').then(r => r.json()).then(j => j.channels.speed.stats.max)")
            row = page.locator(".lab-stats tbody tr", has_text="Speed").inner_text().split("\t")
            check(abs(float(row[3]) - api_max) < 0.06, f"table max equals full-resolution max ({row[3]} vs {api_max:.2f})")
            r0 = page.locator(".lab-bar .replay-time").inner_text()
            box = page.locator(".lab-canvas").bounding_box()
            ya = box["y"] + 60
            page.mouse.move(box["x"] + 132 + (box["width"] - 144) * 0.2, ya)
            page.mouse.down()
            page.mouse.move(box["x"] + 132 + (box["width"] - 144) * 0.6, ya, steps=4)
            page.mouse.up()
            check(wait_until(lambda: page.locator(".lab-bar .replay-time").inner_text() != r0), f"dragging zooms into a range ({r0} -> {page.locator('.lab-bar .replay-time').inner_text()})")
            page.get_by_role("checkbox", name="Engine speed").check()
            check(wait_until(lambda: page.locator(".lab-stats tbody tr").count() == 5), "adding a channel adds a lane and a stats row")
            page.reload()
            page.get_by_role("button", name="Sessions").click()
            page.locator(".session-analyze").click()
            check(wait_until(lambda: page.get_by_role("checkbox", name="Engine speed").is_checked(), timeout=10), "channel choice survives a reload")
            page.get_by_role("checkbox", name="Engine speed").uncheck()
            page.locator(".note-form input").fill("smoke note")
            page.get_by_role("button", name="Add note").click()
            check(wait_until(lambda: "Note added" in page.locator(".note-form").inner_text() and page.locator(".ev-note").count() == 1), "note added and listed")
            page.locator(".ev-note").get_by_role("button", name="Delete").click()
            check(wait_until(lambda: page.locator(".ev-note").count() == 0), "note deleted")
            csv = page.request.get(url.rstrip("/") + page.locator("a", has_text="Export CSV").get_attribute("href"))
            check(csv.status == 200 and csv.text().startswith("sample_index,recording_s"), "CSV export downloads")
            check(page.locator("#gg-title").is_visible() and page.locator("#route-title").is_visible() and page.locator("#power-title").is_visible(), "G-G, route and power panels shown")
            if out:
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(out / "lab-1920.png"), full_page=True)
            page.get_by_role("button", name="Replay from").click()
            check(wait_until(lambda: page.locator(".replay-bar").count() == 1), "Lab opens Replay at the chosen time")
            page.get_by_role("button", name="Close replay").click()
            check(wait_until(lambda: page.locator(".session-row").count() >= 1), "back to Sessions")
            if out:
                page.screenshot(path=str(out / "sessions-1920.png"))
                page.set_viewport_size({"width": 390, "height": 844})
                page.wait_for_timeout(400)
                check(page.evaluate("document.documentElement.scrollWidth") <= 390, "no horizontal scroll on Sessions at 390 px")
                page.screenshot(path=str(out / "sessions-390.png"), full_page=True)
                page.set_viewport_size({"width": 1920, "height": 1080})

            # a recording does not depend on the page: start, close page, stop via API
            page.get_by_role("button", name="Live").click()
            page.locator(".rec-btn").click()
            check(wait_until(lambda: page.locator(".rec-btn-on").count() == 1), "second recording started")
            page.close()
            time.sleep(2.0)
            import json as _json
            import urllib.request as _rq
            st = _json.load(_rq.urlopen(f"{url}api/v1/health"))["recorder"]
            check(st["state"] == "recording" and st["samples"] > 60, f"recording continues with no page open ({st['state']}, {st['samples']} samples)")
            page = ctx.new_page()
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(url)
            page.wait_for_selector(".speed-num", timeout=10_000)
            check(wait_until(lambda: page.locator(".rec-btn-on").count() == 1), "reopened page shows the running recording")
            page.locator(".rec-btn").click()
            check(wait_until(lambda: page.locator(".rec-btn-on").count() == 0), "recording stopped from the reopened page")

            # diagnostic capture from the UI
            page.get_by_role("button", name="Diagnostics").click()
            page.get_by_role("button", name="Capture 30 s").click()
            check(wait_until(lambda: "Capturing" in page.locator("#capture-title").locator("xpath=..").inner_text()), "capture runs")
            page.wait_for_timeout(1500)
            page.evaluate("fetch('/api/v1/diagnostics/capture/stop', {method: 'POST'})")
            check(wait_until(lambda: "Last capture" in page.locator("#capture-title").locator("xpath=..").inner_text()), "capture summary shown")
            if out:
                page.screenshot(path=str(out / "diagnostics-capture-1920.png"), full_page=True)
            page.get_by_role("button", name="Live").click()
            page.wait_for_selector(".speed-num")
            inst0 = page.evaluate("fetch('/api/v1/build').then(r => r.json()).then(j => j.server_instance_id)")
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
            inst1 = page.evaluate("fetch('/api/v1/build').then(r => r.json()).then(j => j.server_instance_id)")
            check(inst1 != inst0, "restarted server reports a new instance id")
            check(page.locator(".banner-info").count() == 0, "no false 'newer UI' banner when the bundle did not change")

        real_errors = [e for e in errors if "WebSocket" not in e and "ERR_CONNECTION_REFUSED" not in e]
        check(not real_errors, f"no console/page errors apart from the deliberate server stop ({real_errors[:3]})")
        browser.close()

    print(f"\n{'OK' if not failures else 'FAILED'}: {len(failures)} failed check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
