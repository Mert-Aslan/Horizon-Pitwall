"""Send synthetic FH6-format packets to a UDP port (testing without the game).

    python tools/send_synthetic.py --port 15607 --hz 60 --seconds 30

Refuses the game's default port 5607 unless --allow-default-port is given, so fixtures
are never sent to a running Pitwall/game setup by accident. CarOrdinal is -2 (never a
real car). Options to inject bad traffic: --bad-length N sends N-byte datagrams instead.
"""

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.telemetry.synth import build_packet, demo_values  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--hz", type=float, default=60.0)
    ap.add_argument("--seconds", type=float, default=10.0)
    ap.add_argument("--bad-length", type=int, default=0)
    ap.add_argument("--allow-default-port", action="store_true")
    args = ap.parse_args()
    if args.port == 5607 and not args.allow_default_port:
        print("refusing to send to 5607 without --allow-default-port", file=sys.stderr)
        return 2
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    t0 = time.monotonic()
    nxt = t0
    sent = 0
    while (now := time.monotonic()) - t0 < args.seconds:
        vals = demo_values(now - t0 + 1.0)
        vals["CarOrdinal"] = -2
        data = build_packet(vals) if not args.bad_length else build_packet(vals, length=args.bad_length)
        tx.sendto(data, (args.host, args.port))
        sent += 1
        nxt += 1 / args.hz
        time.sleep(max(0.0, nxt - time.monotonic()))
    print(f"sent {sent} packets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
