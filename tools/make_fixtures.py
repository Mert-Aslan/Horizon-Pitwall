"""Regenerate the synthetic regression fixtures in fixtures/synthetic/.

    python tools/make_fixtures.py

Each fixture is a single datagram (.bin) plus an entry in manifest.json describing its
origin and the expected decoder outcome. Packets are written with hand-coded
struct.pack_into offsets (tests/packets.py), independent of backend/telemetry/schema.py.
All values are invented; CarOrdinal is negative (never a real car). No real-game
capture exists yet: see docs/PROTOCOL.md "Real game validation".
"""

import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.packets import packet  # noqa: E402

OUT = ROOT / "fixtures" / "synthetic"

CASES = [
    ("valid_324_driving", packet(), "accepted", "plausible driving values, 4th gear, 6100/8000 rpm"),
    ("valid_324_zero_speed", packet(Speed=0.0, Accel=0, Gear=1), "accepted", "measured zero speed (must stay 0, not missing)"),
    ("valid_324_gear0", packet(Gear=0, Speed=-2.0), "accepted", "gear code 0 (R/N encoding unverified)"),
    ("valid_324_nan_rpm", packet(CurrentEngineRpm=math.nan), "accepted", "NaN rpm -> rpm and rpm_fraction invalid"),
    ("valid_324_inf_speed", packet(Speed=math.inf), "accepted", "+Inf speed -> speed invalid"),
    ("valid_324_steer_min", packet(Steer=-128), "accepted", "steer -128, outside documented -127..127"),
    ("valid_324_tail_ff", packet()[:323] + b"\xff", "accepted", "non-zero undocumented trailing byte"),
    ("reject_323", packet(length=323), "length_323_undocumented", "fields only, no trailing byte"),
    ("reject_232", packet(length=232), "too_short", "truncated (FM7 'sled' length)"),
    ("reject_0", b"", "too_short", "empty datagram"),
    ("reject_400", packet() + b"\x00" * 76, "unexpected_length", "oversized"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, data, expected, desc in CASES:
        (OUT / f"{name}.bin").write_bytes(data)
        manifest.append({"file": f"{name}.bin", "length": len(data), "expected": expected, "description": desc, "origin": "synthetic (tools/make_fixtures.py)"})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(manifest)} fixtures to {OUT}")


if __name__ == "__main__":
    main()
