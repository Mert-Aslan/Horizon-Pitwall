"""Regression tests on real Forza Horizon 6 datagrams (fixtures/real, extracted from a
recording of 16 Nov 2026 by tools/extract_real_fixture.py). Besides the manifest's
decoder expectations, a few facts are checked straight from the bytes with the
hand-written offsets in tests/packets.py, so a schema mistake cannot hide."""

import json
import struct
from pathlib import Path

import pytest

from backend.telemetry import decode as dec

from .packets import OFF

DIR = Path(__file__).resolve().parents[1] / "fixtures" / "real"
MANIFEST = json.loads((DIR / "manifest.json").read_text(encoding="utf-8"))


def field(data: bytes, name: str):
    off, fmt = OFF[name]
    return struct.unpack_from(fmt, data, off)[0]


@pytest.mark.parametrize("entry", MANIFEST["files"], ids=lambda e: e["file"])
def test_real_datagram_decodes_as_recorded(entry):
    data = (DIR / entry["file"]).read_bytes()
    assert len(data) == 324 and data[323:] == b"\x00"  # P-1: documented length, zero tail
    res = dec.decode(data)
    assert res.ok
    n, d, q = dec.normalize(res.raw)
    e = entry["expect"]
    assert q["flags"] == e["flags"]
    assert n["gear"]["raw"] == e["gear_raw"] and n["gear"]["label"] == e["gear_label"]
    assert n["race_on"] == e["race_on"]
    assert (d["speed_kmh"] is None) == e["speed_kmh_is_null"]
    assert n["inputs"]["steer"] == e["steer"] and n["inputs"]["brake"] == e["brake"]


def bytes_of(name):
    return (DIR / f"{name}.bin").read_bytes()


def test_empty_packet_is_all_zero_except_timestamp():
    data = bytes_of("empty_race_off")
    assert field(data, "IsRaceOn") == 0 and field(data, "TimestampMS") > 0
    assert data[8:323] == bytes(315)


def test_little_endian_values_are_plausible():
    # P-2: read with little-endian offsets written by hand, values make physical sense
    data = bytes_of("forward_gear4_fast")
    assert 1000 < field(data, "EngineMaxRpm") < 20000
    assert 100 <= field(data, "CarPerformanceIndex") <= 999
    assert field(data, "CarClass") in range(8) and field(data, "DrivetrainType") in (0, 1, 2)
    assert field(data, "Speed") > 70


def test_gear_codes_straight_from_bytes():
    assert field(bytes_of("reverse_gear0"), "Gear") == 0
    assert field(bytes_of("between_gears11"), "Gear") == 11


def test_steering_extremes_are_plus_minus_127():
    # P-6: the real game used -127 / 127 (no -128 in 24 285 driving samples)
    assert field(bytes_of("steer_full_left"), "Steer") == -127
    assert field(bytes_of("steer_full_right"), "Steer") == 127
