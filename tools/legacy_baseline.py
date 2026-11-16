"""Phase 0 baseline: run the untouched legacy dashboard on isolated ports and feed it
synthetic packets, so its behaviour/appearance can be reproduced and screenshotted.

Usage:
    python tools/legacy_baseline.py [--udp 15607] [--web 18080] [--seconds 120]

The legacy module is loaded from legacy/forza_horizon_6_telemetry.original.py; only its
module-level port constants are overridden. The game's port (5607) is never used.
"""

import argparse
import importlib.util
import math
import socket
import struct
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "legacy" / "forza_horizon_6_telemetry.original.py"


def synthetic_packet(t: float) -> bytes:
    """Hand-written packer (independent of backend schema)."""
    buf = bytearray(324)
    struct.pack_into("<i", buf, 0, 1)
    struct.pack_into("<I", buf, 4, int(t * 1000) & 0xFFFFFFFF)
    struct.pack_into("<fff", buf, 8, 8000.0, 900.0, 6100.0 + 900 * math.sin(t))
    struct.pack_into("<i", buf, 212, 1234)
    struct.pack_into("<iiii", buf, 216, 3, 700, 2, 6)
    struct.pack_into("<fff", buf, 256, 45.0 + 3 * math.sin(t), 250000.0, 420.0)
    struct.pack_into("<ffff", buf, 268, 180.0, 182.0, 175.0, 176.0)
    struct.pack_into("<BBBBBb", buf, 315, 184, 0, 0, 0, 4, -23)
    return bytes(buf)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--udp", type=int, default=15607)
    ap.add_argument("--web", type=int, default=18080)
    ap.add_argument("--seconds", type=float, default=120)
    args = ap.parse_args()

    spec = importlib.util.spec_from_file_location("legacy_dashboard", LEGACY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.UDP_PORT = args.udp
    mod.WEB_PORT = args.web

    threading.Thread(target=mod.udp_listener, daemon=True).start()
    threading.Thread(target=mod.start_web_server, daemon=True).start()
    time.sleep(0.5)

    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.monotonic()
    while time.monotonic() - t0 < args.seconds:
        tx.sendto(synthetic_packet(time.monotonic() - t0), ("127.0.0.1", args.udp))
        time.sleep(1 / 60)


if __name__ == "__main__":
    main()
