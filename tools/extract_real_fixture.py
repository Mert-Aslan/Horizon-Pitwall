"""Extract a small real-game regression fixture from a Pitwall recording (read-only).

    python tools/extract_real_fixture.py <recording_id> [--data-dir DIR] [--out fixtures/real]

Picks one datagram per case (empty IsRaceOn=0 packet, reverse, between-gears code,
forward gear at speed, full throttle, full brake, steering at both ends) and writes them
as .bin files plus manifest.json with the expected decode results. The datagrams are the
game's own bytes; they contain car telemetry only (no names, no network addresses).
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.telemetry import decode as dec  # noqa: E402

CASES = {
    "empty_race_off": lambda r: dec.is_empty_packet(r),
    "reverse_gear0": lambda r: r["IsRaceOn"] == 1 and r["Gear"] == 0 and r["VelocityZ"] < -1,
    "between_gears11": lambda r: r["IsRaceOn"] == 1 and r["Gear"] == 11 and r["Speed"] > 20,
    "forward_gear4_fast": lambda r: r["IsRaceOn"] == 1 and r["Gear"] == 4 and r["Speed"] > 70,
    "full_brake": lambda r: r["IsRaceOn"] == 1 and r["Brake"] == 255,
    "steer_full_left": lambda r: r["IsRaceOn"] == 1 and r["Steer"] == -127,
    "steer_full_right": lambda r: r["IsRaceOn"] == 1 and r["Steer"] == 127,
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("recording_id")
    ap.add_argument("--data-dir", default=str(Path(os.environ.get("LOCALAPPDATA", ".")) / "HorizonPitwall"))
    ap.add_argument("--out", default=str(ROOT / "fixtures" / "real"))
    a = ap.parse_args()
    rec = Path(a.data_dir) / "recordings" / a.recording_id
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    found: dict[str, tuple[dict, bytes]] = {}
    for f in sorted(rec.glob("chunk-*.jsonl.gz")):
        with gzip.open(f, "rt", encoding="ascii") as fh:
            for line in fh:
                o = json.loads(line)
                data = base64.b64decode(o["d"])
                res = dec.decode(data)
                if not res.ok:
                    continue
                for name, pred in CASES.items():
                    if name not in found and pred(res.raw):
                        found[name] = (o, data)
        if len(found) == len(CASES):
            break
    manifest = {"source": f"Pitwall recording {a.recording_id} (Forza Horizon 6, PC, 16 Nov 2026, one session, one car)",
                "note": "Real game bytes. Expectations describe this app's decoding; game semantics for gear codes are observations.",
                "files": []}
    for name, (o, data) in found.items():
        (out / f"{name}.bin").write_bytes(data)
        n, d, q = dec.normalize(dec.decode(data).raw)
        manifest["files"].append({
            "file": f"{name}.bin", "length": len(data), "sample_index": o["i"], "game_timestamp_ms": o["ts"],
            "expect": {"flags": q["flags"], "gear_raw": n["gear"]["raw"], "gear_label": n["gear"]["label"],
                       "race_on": n["race_on"], "speed_kmh_is_null": d["speed_kmh"] is None,
                       "steer": n["inputs"]["steer"], "brake": n["inputs"]["brake"], "tail_hex": data[323:].hex()},
        })
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    missing = sorted(set(CASES) - set(found))
    print(f"wrote {len(found)} fixtures to {out}" + (f"; not found: {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
