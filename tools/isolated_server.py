"""Run an isolated Pitwall instance for tests, then stop exactly the processes it started.

Replaces the webapp-testing skill's with_server.py for this project on Windows: that helper
starts servers through a shell and terminate() stops only cmd.exe, leaving Pitwall running.
Here the server is started directly (no shell), its PID is tracked, and on exit the
process *and its descendants* are stopped (psutil), never any other python/node process.
Afterwards the test ports are checked to be free.

    python tools/isolated_server.py --web-port 18100 --udp-port 15620 [--demo] [--dist DIR] \
        [--feed SECONDS] [--feed-t0 26] -- python tools/ui_smoke.py --url http://127.0.0.1:18100/

--feed sends the deterministic synthetic sequence (CarOrdinal -2, live mode) for SECONDS
before the command starts and keeps sending until it ends, so screenshots taken by the
command see comparable data. Ports 5607 and 8080 (the user's game/app) are refused.
"""

from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows consoles default to cp125x

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.telemetry.synth import build_packet, demo_values  # noqa: E402

PROTECTED_PORTS = {5607, 8080}


def port_in_use(port: int, kind: int) -> bool:
    s = socket.socket(socket.AF_INET, kind)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        s.bind(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


class IsolatedPitwall:
    def __init__(self, web_port: int, udp_port: int, demo: bool = False, dist: str | None = None, log: Path | None = None) -> None:
        if {web_port, udp_port} & PROTECTED_PORTS:
            raise SystemExit(f"refusing protected port(s) {sorted({web_port, udp_port} & PROTECTED_PORTS)}")
        self.web_port, self.udp_port, self.demo, self.dist = web_port, udp_port, demo, dist
        self.log = log or Path(os.environ.get("TEMP", ".")) / f"pitwall-isolated-{web_port}.log"
        self.proc: subprocess.Popen | None = None

    def __enter__(self) -> "IsolatedPitwall":
        if port_in_use(self.web_port, socket.SOCK_STREAM) or port_in_use(self.udp_port, socket.SOCK_DGRAM):
            raise SystemExit(f"test port busy: web {self.web_port} / udp {self.udp_port}; not starting")
        env = dict(os.environ)
        if self.dist:
            env["PITWALL_UI_DIST"] = str(Path(self.dist).resolve())
        args = [sys.executable, str(ROOT / "forza_horizon_6_telemetry.py"), "--udp-port", str(self.udp_port), "--web-port", str(self.web_port),
                "--data-dir", str(Path(os.environ.get("TEMP", ".")) / "pitwall-isolated")]
        if self.demo:
            args.append("--demo")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        self._logf = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(args, cwd=ROOT, env=env, stdout=self._logf, stderr=subprocess.STDOUT, creationflags=flags)
        print(f"[isolated] started pid {self.proc.pid} web :{self.web_port} udp :{self.udp_port}", flush=True)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"server exited early (code {self.proc.returncode}); see {self.log}")
            with contextlib.suppress(OSError):
                with urllib.request.urlopen(f"http://127.0.0.1:{self.web_port}/api/v1/health", timeout=1):
                    return self
            time.sleep(0.2)
        self.__exit__(None, None, None)
        raise SystemExit("server did not become ready")

    def __exit__(self, *exc) -> None:
        if self.proc is None:
            return
        try:
            root = psutil.Process(self.proc.pid)
            tree = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            tree = []
        for p in tree:
            with contextlib.suppress(psutil.Error):
                p.terminate()
        gone, alive = psutil.wait_procs(tree, timeout=5)
        for p in alive:
            with contextlib.suppress(psutil.Error):
                p.kill()
        psutil.wait_procs(alive, timeout=5)
        self._logf.close()
        print(f"[isolated] stopped {len(tree)} process(es): {[p.pid for p in tree]}", flush=True)
        time.sleep(0.3)
        busy = [n for n, k, port in (("web", socket.SOCK_STREAM, self.web_port), ("udp", socket.SOCK_DGRAM, self.udp_port)) if port_in_use(port, k)]
        print(f"[isolated] ports {'BUSY: ' + ','.join(busy) if busy else 'free'} (web {self.web_port}, udp {self.udp_port})", flush=True)
        self.proc = None


class Feeder(threading.Thread):
    """Deterministic synthetic sequence at 60 Hz, starting at demo time t0."""

    def __init__(self, port: int, t0: float) -> None:
        super().__init__(daemon=True)
        self.port, self.t0 = port, t0
        self.stop_ev = threading.Event()

    def run(self) -> None:
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        start = time.monotonic()
        nxt = start
        while not self.stop_ev.is_set():
            t = self.t0 + time.monotonic() - start
            vals = demo_values(t)
            vals["CarOrdinal"] = -2
            vals["TimestampMS"] = int(t * 1000)
            tx.sendto(build_packet(vals), ("127.0.0.1", self.port))
            nxt += 1 / 60
            self.stop_ev.wait(max(0.0, nxt - time.monotonic()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-port", type=int, required=True)
    ap.add_argument("--udp-port", type=int, required=True)
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--dist", help="serve this UI bundle instead of frontend/dist")
    ap.add_argument("--feed", type=float, default=0.0, help="seconds of synthetic data before the command")
    ap.add_argument("--feed-t0", type=float, default=6.0, help="demo-loop time the feed starts at")
    ap.add_argument("command", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not cmd:
        ap.error("no command given")
    with IsolatedPitwall(a.web_port, a.udp_port, a.demo, a.dist):
        feeder = None
        if a.feed:
            feeder = Feeder(a.udp_port, a.feed_t0)
            feeder.start()
            time.sleep(a.feed)
        try:
            return subprocess.run(cmd, cwd=ROOT).returncode
        finally:
            if feeder:
                feeder.stop_ev.set()
                feeder.join(2)


if __name__ == "__main__":
    sys.exit(main())
