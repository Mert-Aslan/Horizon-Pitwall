"""End-to-end test of the packaged release ZIP (what a user downloads).

    python tools/test_exe.py [--zip dist-release/HorizonPitwall-<v>-windows-x64.zip] [--no-tray] [--keep]

Extracts the ZIP into a temporary folder whose path contains spaces and non-ASCII
characters, then runs HorizonPitwall.exe with a stripped environment (no Python on
PATH, no PYTHON* variables, LOCALAPPDATA pointing into the test folder) and isolated
free ports. Nothing is sent to the game's port 5607 and nothing outside the test folder
is touched; only processes started from the extracted folder are ever stopped.

Writes a JSON report next to the ZIP and exits non-zero if any check failed.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import APP_VERSION  # noqa: E402
from tests.packets import packet  # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TEST_PREFIX = "pitwall-exe-test-"


def free_port(kind: int = socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def port_free(port: int, kind: int = socket.SOCK_STREAM) -> bool:
    s = socket.socket(socket.AF_INET, kind)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


class Http:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def req(self, method: str, path: str, body: Any = None, timeout: float = 10) -> tuple[int, bytes]:
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json", "Origin": self.base} if data is not None else {"Origin": self.base})
        try:
            with OPENER.open(r, timeout=timeout) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def json(self, method: str, path: str, body: Any = None) -> Any:
        code, raw = self.req(method, path, body)
        if code >= 400:
            raise AssertionError(f"{method} {path} -> {code} {raw[:200]!r}")
        return json.loads(raw)


class Sender:
    """Synthetic Data Out packets at ~60 Hz to the isolated UDP port."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.ts = 1000
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stop = threading.Event()
        self.thread: Optional[threading.Thread] = None

    def burst(self, n: int, hz: float = 60) -> None:
        for _ in range(n):
            self.ts += int(1000 / hz)
            self.sock.sendto(packet(TimestampMS=self.ts, IsRaceOn=1, Speed=30.0, CurrentEngineRpm=4000.0, CarOrdinal=4242),
                             ("127.0.0.1", self.port))
            time.sleep(1 / hz)

    def start(self) -> None:
        self.stop.clear()

        def run() -> None:
            while not self.stop.is_set():
                self.burst(1)

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def halt(self) -> None:
        self.stop.set()
        if self.thread:
            self.thread.join(5)


class Harness:
    def __init__(self, zip_path: Path, no_tray: bool) -> None:
        self.zip = zip_path
        self.no_tray = no_tray
        self.tmp = Path(tempfile.mkdtemp(prefix=TEST_PREFIX))
        self.work = self.tmp / "Pitwall Test ğüş"
        self.local = self.work / "Local App Data é"
        self.data = self.local / "HorizonPitwall"
        self.web, self.udp = free_port(), free_port(socket.SOCK_DGRAM)
        self.http = Http(f"http://127.0.0.1:{self.web}")
        self.results: list[dict[str, Any]] = []
        self.procs: list[subprocess.Popen] = []
        self.manifest: dict[str, Any] = {}
        self.app: Path = Path()

    # ------------------------------------------------------------ setup
    def extract(self) -> None:
        dest = self.work / "Programs ü"
        dest.mkdir(parents=True)
        with zipfile.ZipFile(self.zip) as zf:
            zf.extractall(dest)
        tops = list(dest.iterdir())
        assert len(tops) == 1 and tops[0].name == f"HorizonPitwall-{APP_VERSION}", tops
        self.app = tops[0]
        mf = self.zip.with_name(f"HorizonPitwall-{APP_VERSION}-release-manifest.json")
        self.manifest = json.loads(mf.read_text(encoding="utf-8")) if mf.exists() else {}
        self.data.mkdir(parents=True)
        self.write_config({})

    def write_config(self, extra: dict[str, Any]) -> None:
        (self.data / "pitwall.config.json").write_text(json.dumps({"web_port": self.web, "udp_port": self.udp, **extra}), encoding="utf-8")

    def env(self, **over: str) -> dict[str, str]:
        keep = ("SystemRoot", "SYSTEMDRIVE", "WINDIR", "TEMP", "TMP", "USERPROFILE", "USERNAME", "USERDOMAIN", "COMPUTERNAME",
                "APPDATA", "PROGRAMDATA", "ProgramFiles", "HOMEDRIVE", "HOMEPATH", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS")
        e = {k: os.environ[k] for k in keep if k in os.environ}
        root = os.environ.get("SystemRoot", r"C:\Windows")
        e["PATH"] = f"{root}\\System32;{root}"
        e["LOCALAPPDATA"] = str(self.local)
        e["PITWALL_NO_DIALOGS"] = "1"
        if self.no_tray:
            e["PITWALL_NO_TRAY"] = "1"
        e.update(over)
        return e

    def exe(self, *args: str, env: Optional[dict[str, str]] = None) -> subprocess.Popen:
        p = subprocess.Popen([str(self.app / "HorizonPitwall.exe"), *args], cwd=str(self.tmp), env=env or self.env(),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(p)
        return p

    def run(self, *args: str, timeout: float = 90, env: Optional[dict[str, str]] = None) -> int:
        return self.exe(*args, env=env).wait(timeout)

    # ------------------------------------------------------------ helpers
    def instance(self) -> Optional[dict[str, Any]]:
        try:
            return json.loads((self.data / "run" / "instance.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def wait_ready(self, timeout: float = 40) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            inst = self.instance()
            if inst:
                try:
                    h = self.http.json("GET", "/api/v1/health")
                    if h.get("app") == "Horizon Pitwall" and h["build"]["server_instance_id"] == inst["server_instance_id"]:
                        return inst
                except (OSError, AssertionError, ValueError, KeyError):
                    pass
            time.sleep(0.2)
        raise AssertionError("Pitwall did not become ready")

    def owner_pid(self) -> int:
        inst = self.instance()
        assert inst, "no instance.json"
        return int(inst["pid"])

    def ours(self, pid: int) -> bool:
        import psutil

        try:
            exe = Path(psutil.Process(pid).exe()).resolve()
        except (psutil.Error, OSError):
            return False
        return self.app.resolve() in exe.parents

    def kill(self, pid: int) -> None:
        if not self.ours(pid):
            raise AssertionError(f"refusing to kill PID {pid}: not started from the test folder")
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)

    def wait_ports_free(self, timeout: float = 20) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if port_free(self.web) and port_free(self.udp, socket.SOCK_DGRAM):
                return True
            time.sleep(0.2)
        return False

    def check(self, name: str, fn: Callable[[], Any]) -> None:
        t0 = time.monotonic()
        try:
            detail = fn()
            ok = True
        except Exception as e:  # noqa: BLE001 - reported
            detail, ok = f"{type(e).__name__}: {e}", False
        self.results.append({"check": name, "ok": ok, "seconds": round(time.monotonic() - t0, 2), "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)

    def cleanup(self, keep: bool) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.kill()
        inst = self.instance()
        if inst and self.ours(int(inst["pid"])):
            self.kill(int(inst["pid"]))
        if keep:
            print(f"kept {self.tmp}")
            return
        import shutil

        base = Path(tempfile.gettempdir()).resolve()
        t = self.tmp.resolve()
        if t.parent == base and t.name.startswith(TEST_PREFIX):
            for _ in range(10):
                try:
                    shutil.rmtree(t)
                    break
                except OSError:
                    time.sleep(0.5)

    # ------------------------------------------------------------ scenarios
    def record(self, sender: Sender, seconds: float, title: str) -> str:
        st = self.http.json("POST", "/api/v1/recordings/start", {"title": title})
        sender.burst(int(seconds * 60))
        return st["recording_id"]

    def scenario(self) -> None:
        h = self
        s = Sender(self.udp)
        files_before = sorted(str(p.relative_to(self.app)) for p in self.app.rglob("*"))

        def paths_cmd():
            assert h.run("--paths") == 0
            return None

        h.check("--paths exits 0", paths_cmd)

        def start():
            t0 = time.monotonic()
            h.exe("--background")
            inst = h.wait_ready()
            return f"ready in {time.monotonic() - t0:.1f}s, pid {inst['pid']}"

        h.check("start (background) and verified readiness", start)

        def build():
            b = h.http.json("GET", "/api/v1/build")
            assert b["frozen"] is True and b["app_version"] == APP_VERSION, b
            mb = h.manifest.get("build", {})
            if mb:
                assert b["commit"] == mb["commit"], (b["commit"], mb["commit"])
                assert b["backend_digest_full"] == mb["source_digest"]
            assert b["ui"] and b["ui"]["ui_version"] == APP_VERSION
            return b["build_id"]

        h.check("build identity matches release manifest", build)

        def assets():
            code, html = h.http.req("GET", "/")
            assert code == 200 and b"<div id=\"root\"" in html
            import re

            refs = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', html)
            assert refs, "no assets referenced"
            for r in refs:
                c, body = h.http.req("GET", r.decode())
                assert c == 200 and body, r
            return f"{len(refs)} assets"

        h.check("dashboard and assets served", assets)

        def ws():
            from websockets.sync.client import connect

            with connect(f"ws://127.0.0.1:{h.web}/api/v1/stream", origin=f"http://127.0.0.1:{h.web}", open_timeout=10) as c:
                hello = json.loads(c.recv(timeout=10))
            assert hello["type"] == "hello" and hello["server_instance_id"] == h.instance()["server_instance_id"]
            return None

        h.check("websocket stream hello", ws)

        def udp():
            s.burst(90)
            time.sleep(0.5)
            latest = h.http.json("GET", "/api/v1/telemetry/latest")
            txt = json.dumps(latest)
            assert "4242" in txt, txt[:300]
            return None

        h.check("UDP telemetry received", udp)

        def recording():
            rid = h.record(s, 3, "exe test")
            h.http.json("POST", "/api/v1/recordings/stop")
            rec = h.http.json("GET", f"/api/v1/recordings/{rid}")
            assert rec["status"] == "completed" and rec["samples"] > 100, rec
            fr = h.http.req("GET", f"/api/v1/recordings/{rid}/frames?from=0&to=60")
            assert fr[0] == 200 and len(fr[1]) > 1000
            assert h.http.req("GET", f"/api/v1/recordings/{rid}/lab/summary")[0] == 200
            c, csv = h.http.req("GET", f"/api/v1/recordings/{rid}/lab/export.csv?channels=speed,rpm")
            assert c == 200 and csv.count(b"\n") > 100, csv[:200]
            h.http.json("PUT", "/api/v1/cars/4242", {"name": "Exe Test Car"})
            assert "Exe Test Car" in json.dumps(h.http.json("GET", "/api/v1/cars"))
            return f"{rec['samples']} samples"

        h.check("record, replay frames, lab, CSV, car names", recording)

        def second():
            pid = h.owner_pid()
            assert h.run("--background") == 0
            ps = [h.exe("--background") for _ in range(3)]
            assert all(p.wait(90) == 0 for p in ps)
            assert h.owner_pid() == pid
            return None

        h.check("second and concurrent launches reuse the owner", second)

        def exit_during_recording():
            pid = h.owner_pid()
            rid = h.record(s, 1, "exit test")
            s.start()
            time.sleep(1)
            t0 = time.monotonic()
            code = h.run("--exit")
            s.halt()
            assert code == 0, code
            import psutil

            # --exit returns once the server stopped and the single-instance lock is free;
            # interpreter teardown can take a moment longer.
            deadline = time.monotonic() + 15
            while psutil.pid_exists(pid) and h.ours(pid) and time.monotonic() < deadline:
                time.sleep(0.2)
            assert not psutil.pid_exists(pid) or not h.ours(pid), "process still running 15 s after --exit"
            assert h.wait_ports_free(), "ports still in use"
            assert h.instance() is None, "instance.json left behind"
            took = time.monotonic() - t0
            h.exe("--background")
            h.wait_ready()
            rec = h.http.json("GET", f"/api/v1/recordings/{rid}")
            assert rec["status"] == "completed" and rec["samples"] > 60, rec
            return f"exit took {took:.1f}s, {rec['samples']} samples, stop_reason={rec.get('stop_reason')}"

        h.check("--exit during recording flushes and frees ports", exit_during_recording)

        def crash():
            pid = h.owner_pid()
            rid = h.record(s, 2, "crash test")
            h.kill(pid)
            time.sleep(1)
            assert h.wait_ports_free(), "ports still in use after kill"
            h.exe("--background")
            h.wait_ready()
            rec = h.http.json("GET", f"/api/v1/recordings/{rid}")
            assert rec["status"] in ("recovered", "incomplete") and rec["samples"] > 0, rec
            assert h.owner_pid() != pid
            return f"status {rec['status']}, {rec['samples']} samples"

        h.check("killed process: next start recovers the recording", crash)

        def stop():
            assert h.run("--exit") == 0
            assert h.wait_ports_free()
            return None

        h.check("final --exit", stop)

        def not_running():
            assert h.run("--exit") == 1
            return None

        h.check("--exit when not running exits 1", not_running)

        def bad_config():
            h.write_config({"stale_after_ms": "soon"})
            try:
                assert h.run("--background") == 2
            finally:
                h.write_config({})
            return None

        h.check("invalid settings exit 2", bad_config)

        def web_conflict():
            sock = socket.socket()
            sock.bind(("127.0.0.1", h.web))
            sock.listen()
            try:
                assert h.run("--background") == 3
            finally:
                sock.close()
            return None

        h.check("dashboard port in use exits 3", web_conflict)

        def udp_conflict():
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.bind(("127.0.0.1", h.udp))
            try:
                assert h.run("--background") == 3
            finally:
                sock.close()
            return None

        h.check("telemetry port in use exits 3", udp_conflict)

        def missing_ui():
            idx = h.app / "_internal" / "frontend" / "dist" / "index.html"
            moved = idx.with_name("index.html.moved")
            idx.rename(moved)
            try:
                assert h.run("--background") == 4
            finally:
                moved.rename(idx)
            return None

        h.check("missing dashboard files exit 4", missing_ui)

        def denied_data():
            f = h.work / "not a folder"
            f.write_text("x")
            assert h.run("--background", env=h.env(LOCALAPPDATA=str(f))) == 6
            return None

        h.check("unusable data folder exits 6", denied_data)

        def service_exe():
            assert h.run("--service-host") == 2  # only the Service Control Manager may use it
            assert h.run("service", "status") == 0
            return None

        h.check("service host refuses manual start; service status works", service_exe)

        def clean_folder():
            after = sorted(str(p.relative_to(h.app)) for p in h.app.rglob("*"))
            assert after == files_before, set(after) ^ set(files_before)
            log = (h.data / "logs" / "pitwall.log").read_text(encoding="utf-8", errors="replace")
            assert "event=launcher_exit" in log
            if not h.no_tray:
                assert "tray icon unavailable" not in log, "tray failed to start"
            return f"log {len(log)} bytes"

        h.check("program folder untouched; log written to data folder", clean_folder)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=str(ROOT / "dist-release" / f"HorizonPitwall-{APP_VERSION}-windows-x64.zip"))
    ap.add_argument("--no-tray", action="store_true", help="run without the notification-area icon (headless CI)")
    ap.add_argument("--keep", action="store_true", help="keep the temporary test folder")
    a = ap.parse_args()
    z = Path(a.zip).resolve()
    if not z.exists():
        raise SystemExit(f"{z} not found; run tools/build_exe.py first")
    h = Harness(z, a.no_tray)
    try:
        h.check("extract ZIP to a path with spaces and non-ASCII characters", h.extract)
        if h.results[-1]["ok"]:
            h.scenario()
    finally:
        h.cleanup(a.keep)
    failed = [r for r in h.results if not r["ok"]]
    report = {"zip": z.name, "version": APP_VERSION, "tray": not a.no_tray, "passed": len(h.results) - len(failed),
              "failed": len(failed), "results": h.results}
    out = z.with_name("exe-test-report.json")
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    print(f"\n{report['passed']} passed, {report['failed']} failed -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
