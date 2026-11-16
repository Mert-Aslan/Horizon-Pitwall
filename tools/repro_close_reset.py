"""Reproduce the Windows "WinError 10054 in _call_connection_lost" log traceback.

Opens a WebSocket to an isolated Pitwall, reads the hello, then resets the TCP connection
(SO_LINGER 0 -> RST), like a browser tab that is killed or a laptop that sleeps. Reports
how many tracebacks the server logged and what /api/v1/diagnostics says afterwards.

    python tools/repro_close_reset.py --web-port 18120 --udp-port 15640 [--count 10]

Runs its own isolated server (tools/isolated_server.py); ports 5607/8080 are refused.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from isolated_server import IsolatedPitwall  # noqa: E402


def reset_once(port: int) -> None:
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(
        (
            f"GET /api/v1/stream HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        ).encode()
    )
    s.recv(4096)  # 101 + maybe hello
    time.sleep(0.3)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()  # RST


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-port", type=int, default=18120)
    ap.add_argument("--udp-port", type=int, default=15640)
    ap.add_argument("--count", type=int, default=10)
    a = ap.parse_args()
    srv = IsolatedPitwall(a.web_port, a.udp_port)
    with srv:
        for _ in range(a.count):
            reset_once(a.web_port)
            time.sleep(0.2)
        time.sleep(1.0)
        diag = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.web_port}/api/v1/diagnostics"))
        health = json.load(urllib.request.urlopen(f"http://127.0.0.1:{a.web_port}/api/v1/health"))
    log = srv.log.read_text(encoding="utf-8", errors="replace")
    tracebacks = log.count("Exception in callback")
    print(f"resets sent: {a.count}")
    print(f"tracebacks in server output: {tracebacks} (10054: {log.count('10054')})")
    print(f"server healthy afterwards: {health['status']}; stream stats: {diag.get('stream')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
