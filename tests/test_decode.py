import json
import math
import struct

import pytest

from backend.telemetry import decode as dec
from backend.telemetry import schema
from backend.telemetry.synth import build_packet

from .packets import OFF, packet


def full(data: bytes):
    r = dec.decode(data)
    assert r.ok, r.reject_reason
    n, d, q = dec.normalize(r.raw)
    return r, n, d, q


# ---------------------------------------------------------------- schema / offsets


def test_schema_matches_document_shape():
    assert len(schema.FIELDS) == 88
    assert schema.FIELDS_END == 323
    assert schema.DOCUMENTED_PACKET_LENGTH == 324


@pytest.mark.parametrize("name", sorted(OFF))
def test_schema_offsets_match_independent_table(name):
    off, fmt = OFF[name]
    f = schema.FIELD_BY_NAME[name]
    assert f.offset == off
    assert struct.calcsize(fmt) == f.size


@pytest.mark.parametrize(
    "name,value",
    [
        ("IsRaceOn", 1),
        ("TimestampMS", 0xFFFFFFF0),
        ("CarOrdinal", 123456),
        ("CarGroup", 0xDEADBEEF),
        ("SmashableMass", 512.5),
        ("PositionX", -1234.5),
        ("Speed", 61.25),
        ("LapNumber", 65535),
        ("RacePosition", 12),
        ("Accel", 255),
        ("Gear", 7),
        ("Steer", -127),
        ("NormalizedAIBrakeDifference", 127),
    ],
)
def test_critical_fields_roundtrip_through_independent_writer(name, value):
    r = dec.decode(packet(**{name: value}))
    assert r.ok
    assert r.raw[name] == pytest.approx(value)


def test_little_endian_is_used():
    buf = bytearray(packet())
    struct.pack_into(">I", buf, 4, 1)  # big-endian 1 == little-endian 16777216
    r = dec.decode(bytes(buf))
    assert r.raw["TimestampMS"] == 1 << 24


def test_tail_byte_is_kept_opaque():
    data = packet()[:323] + b"\x7f"
    r = dec.decode(data)
    assert r.ok and r.tail == b"\x7f"
    assert set(r.raw) == {f.name for f in schema.FIELDS}


# ---------------------------------------------------------------- lengths


@pytest.mark.parametrize("length,reason", [(0, "too_short"), (4, "too_short"), (232, "too_short"), (323, "length_323_undocumented"), (400, "unexpected_length"), (311, "too_short"), (331, "unexpected_length")])
def test_unsupported_lengths_are_rejected(length, reason):
    r = dec.decode(packet(length=length) if length <= 324 else packet() + b"\x00" * (length - 324))
    assert not r.ok
    assert r.reject_reason == reason
    assert r.raw == {}


# ---------------------------------------------------------------- normalisation


def test_measured_zero_speed_is_zero_not_missing():
    _, n, d, q = full(packet(Speed=0.0))
    assert n["speed_mps"] == 0.0
    assert d["speed_kmh"] == 0.0
    assert "Speed" not in q["fields"]


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_speed_is_invalid_and_propagates(bad):
    _, n, d, q = full(packet(Speed=bad))
    assert n["speed_mps"] is None
    assert d["speed_kmh"] is None and d["speed_mph"] is None
    assert q["fields"]["Speed"]["status"] == "non_finite"
    assert q["invalid_field_count"] == 1


def test_nan_rpm_invalidates_rpm_fraction():
    _, n, d, q = full(packet(CurrentEngineRpm=math.nan, EngineMaxRpm=8000.0))
    assert n["rpm"] is None
    assert d["rpm_fraction"] is None
    assert q["fields"]["CurrentEngineRpm"]["status"] == "non_finite"


@pytest.mark.parametrize("max_rpm", [0.0, -100.0, math.nan])
def test_bad_max_rpm_gives_no_fraction(max_rpm):
    _, n, d, q = full(packet(EngineMaxRpm=max_rpm))
    assert d["rpm_fraction"] is None
    assert "EngineMaxRpm" in q["fields"]


def test_rpm_fraction_is_not_clamped():
    _, _, d, _ = full(packet(CurrentEngineRpm=8400.0, EngineMaxRpm=8000.0))
    assert d["rpm_fraction"] == pytest.approx(1.05)


@pytest.mark.parametrize("raw,expected", [(-127, -1.0), (127, 1.0), (0, 0.0), (-23, -23 / 127)])
def test_steer_in_range(raw, expected):
    _, n, _, q = full(packet(Steer=raw))
    assert n["inputs"]["steer"] == pytest.approx(expected)
    assert "Steer" not in q["fields"]


def test_steer_minus_128_is_flagged_and_clamped_raw_kept():
    r, n, _, q = full(packet(Steer=-128))
    assert r.raw["Steer"] == -128
    assert n["inputs"]["steer"] == -1.0
    assert q["fields"]["Steer"]["status"] == "out_of_range"


def test_inputs_are_fractions():
    _, n, _, _ = full(packet(Accel=255, Brake=0, Clutch=51, HandBrake=255))
    i = n["inputs"]
    assert i["throttle"] == 1.0 and i["brake"] == 0.0 and i["handbrake"] == 1.0
    assert i["clutch"] == pytest.approx(0.2)


def test_gear_zero_is_not_labelled_reverse_or_neutral():
    _, n, _, _ = full(packet(Gear=0))
    assert n["gear"]["raw"] == 0
    assert n["gear"]["label"] is None
    assert n["gear"]["verified"] is False


def test_unknown_enums_keep_code_without_label():
    _, n, _, q = full(packet(CarClass=9, DrivetrainType=5))
    assert n["car"]["class_code"] == 9 and n["car"]["class_label"] is None
    assert n["car"]["drivetrain_code"] == 5 and n["car"]["drivetrain_label"] is None
    assert q["fields"]["CarClass"]["status"] == "out_of_range"


def test_negative_power_and_torque_are_physical_and_kept():
    _, n, d, q = full(packet(Power=-35000.0, Torque=-90.0))
    assert n["power_w"] == -35000.0 and n["torque_nm"] == -90.0
    assert d["power_kw"] == pytest.approx(-35.0)
    assert "Power" not in q["fields"]


def test_lap_time_zero_means_not_applicable():
    _, n, _, q = full(packet(BestLap=0.0))
    assert n["race"]["best_lap_s"] is None
    assert q["fields"]["BestLap"]["status"] == "not_applicable"


def test_tire_temp_is_passed_through_without_conversion():
    _, n, _, _ = full(packet(TireTempFrontLeft=182.5))
    assert n["wheels"]["fl"]["tire_temp"] == 182.5


def test_g_force_uses_standard_gravity():
    _, _, d, _ = full(packet(AccelerationX=9.80665, AccelerationZ=-19.6133))
    assert d["g_lateral"] == pytest.approx(1.0)
    assert d["g_longitudinal"] == pytest.approx(-2.0)


def test_frame_layers_serialize_without_nan():
    r, n, d, q = full(packet(Speed=math.nan, Power=math.inf, CurrentEngineRpm=-math.inf))
    text = json.dumps({"raw": dec.json_safe_raw(r.raw), "n": n, "d": d, "q": q}, allow_nan=False)
    assert "NaN" not in text and "Infinity" not in text


def test_synth_builder_agrees_with_independent_writer():
    a = build_packet({"Speed": 12.5, "Gear": 3, "Steer": -5, "CarOrdinal": -7, "TimestampMS": 99})
    b = dec.decode(a)
    assert b.raw["Speed"] == 12.5 and b.raw["Gear"] == 3 and b.raw["Steer"] == -5
    assert struct.unpack_from("<f", a, 256)[0] == 12.5
    assert len(a) == 324
