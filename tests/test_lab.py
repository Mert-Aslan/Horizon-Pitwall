"""Telemetry Lab: channels agree with the normalizer, decimation keeps peaks, statistics
are exact, gaps/empty packets/jumps are respected, heuristics carry their rules."""

import json
import math
from pathlib import Path

import pytest

from backend.analysis import lab
from backend.recording.store import encode_line
from backend.telemetry import decode as dec
from backend.telemetry.synth import build_packet

REAL = Path(__file__).resolve().parents[1] / "fixtures" / "real"


def line(i, el, seg=0, **values):
    vals = {"IsRaceOn": 1, "TimestampMS": 1000 + i * 16, "EngineMaxRpm": 8000.0, "CarOrdinal": -3, **values}
    return json.loads(encode_line(i, seg, i + 1, vals["TimestampMS"], el, 0, build_packet(vals)))


def empty_line(i, el):
    data = bytearray(324)
    data[4:8] = (1000 + i * 16).to_bytes(4, "little")
    return json.loads(encode_line(i, 0, i + 1, 1000 + i * 16, el, 0, bytes(data)))


def cols(lines):
    return lab.Columns.build(lines)


# ------------------------------------------------------------------ channels
def expected_from_normalize(data: bytes) -> dict:
    n, d, q = dec.normalize(dec.decode(data).raw)
    w = n["wheels"]
    pct = lambda x: None if x is None else x * 100  # noqa: E731
    return {
        "speed": d["speed_kmh"], "rpm": n["rpm"], "gear": n["gear"]["raw"], "throttle": pct(n["inputs"]["throttle"]),
        "brake": pct(n["inputs"]["brake"]), "clutch": pct(n["inputs"]["clutch"]), "handbrake": pct(n["inputs"]["handbrake"]),
        "steer": pct(n["inputs"]["steer"]), "power": d["power_kw"], "torque": n["torque_nm"], "boost": n["boost_psi"],
        "fuel": pct(n["fuel_fraction"]), "g_lat": d["g_lateral"], "g_long": d["g_longitudinal"], "g_vert": d["g_vertical"],
        "yaw_rate": None if n["motion"]["angular_velocity_rad_s"]["y"] is None else math.degrees(n["motion"]["angular_velocity_rad_s"]["y"]),
        **{f"slip_{k}": w[k]["combined_slip"] for k in ("fl", "fr", "rl", "rr")},
        **{f"susp_{k}": pct(w[k]["suspension_norm"]) for k in ("fl", "fr", "rl", "rr")},
        **{f"temp_{k}": w[k]["tire_temp"] for k in ("fl", "fr", "rl", "rr")},
        "pos_x": n["motion"]["position_world_m"]["x"], "pos_z": n["motion"]["position_world_m"]["z"], "pos_y": n["motion"]["position_world_m"]["y"],
    }


@pytest.mark.parametrize("name", sorted(p.stem for p in REAL.glob("*.bin")))
def test_channels_match_normalize_on_real_packets(name):
    data = (REAL / f"{name}.bin").read_bytes()
    col = cols([json.loads(encode_line(0, 0, 1, 0, 0.0, 0, data))])
    exp = expected_from_normalize(data)
    assert set(exp) == {c.id for c in lab.CHANNELS}
    for cid, e in exp.items():
        got = col.ch[cid][0]
        if e is None:
            assert got != got, cid  # NaN
        else:
            assert got == pytest.approx(e, rel=1e-6, abs=1e-9), cid


def test_non_finite_values_are_missing_not_zero():
    col = cols([line(0, 0.0, Speed=math.nan, CurrentEngineRpm=math.inf)])
    assert col.ch["speed"][0] != col.ch["speed"][0] and col.ch["rpm"][0] != col.ch["rpm"][0]


# ------------------------------------------------------------------ series
def test_envelope_keeps_a_single_sample_peak_and_stats_are_exact():
    lines = [line(i, i / 60, Speed=10.0) for i in range(600)]
    lines[333] = line(333, 333 / 60, Speed=100.0)  # one-sample spike: 360 km/h
    s = lab.series(cols(lines), ["speed"], 0, 10, points=20)
    ch = s["channels"]["speed"]
    assert max(v for v in ch["max"] if v is not None) == pytest.approx(360.0)
    st = ch["stats"]
    assert st["valid"] == 600 and st["max"] == pytest.approx(360.0)
    assert st["mean"] == pytest.approx((599 * 36 + 360) / 600)  # all samples, not bucket means
    assert sum(ch["count"]) == 600


def test_gaps_and_empty_packets_are_listed():
    lines = [line(i, i / 60) for i in range(60)]  # 0..~1 s
    lines += [line(60 + i, 3 + i / 60) for i in range(60)]  # gap 1 -> 3 s
    lines += [empty_line(120 + i, 4 + i / 60) for i in range(30)]  # empty 4 -> 4.5 s
    col = cols(lines)
    g = lab.series(col, ["speed"], 0, 4.6, 100)["gaps"]
    kinds = [(x["kind"], round(x["from"], 1), round(x["to"], 1)) for x in g]
    assert ("no_samples", 1.0, 3.0) in kinds
    assert any(k == "no_driving_data" and a == 4.0 for k, a, _ in kinds)
    assert col.ch["speed"][130] != col.ch["speed"][130]  # empty packet: no value, not 0 km/h


# ------------------------------------------------------------------ summary
def test_distance_integrates_speed_and_skips_gaps():
    lines = [line(i, i / 60, Speed=10.0, Accel=255) for i in range(601)]  # 10 s at 10 m/s, full throttle
    lines += [line(601 + i, 20 + i / 60, Speed=10.0) for i in range(61)]  # after a 10 s gap: 1 s more
    sm = lab.summary(cols(lines), 0, 30)
    assert sm["distance_m"] == pytest.approx(110.0, abs=0.5)
    assert sm["covered_s"] == pytest.approx(11.0, abs=0.05)
    assert sm["full_throttle_s"] == pytest.approx(10.0, abs=0.05)
    assert sm["speed_kmh"]["max"] == pytest.approx(36.0)


def test_gear_changes_ignore_between_gear_code():
    gears = [1] * 10 + [11] * 3 + [2] * 10 + [11] * 2 + [3] * 10 + [0] * 5
    sm = lab.summary(cols([line(i, i / 60, Gear=g) for i, g in enumerate(gears)]), 0, 10)
    assert sm["gear_changes"] == 2


# ------------------------------------------------------------------ G-G, route, power
def test_gg_maxima_use_all_samples_even_when_strided():
    lines = [line(i, i / 60, AccelerationX=0.0, AccelerationZ=0.0) for i in range(9000)]
    lines[4501] = line(4501, 4501 / 60, AccelerationX=3 * 9.80665, AccelerationZ=4 * 9.80665)
    g = lab.gg(cols(lines), 0, 200, max_points=1000)
    assert len(g["points"]) <= 1000 and g["stride"] == 9
    assert g["combined_g_max"] == pytest.approx(5.0)


def test_route_splits_at_gaps_jumps_and_empty_packets():
    lines = [line(i, i / 60, Speed=36.0, PositionX=i * 10 / 60 / 3.6 * 3.6, PositionZ=0.0) for i in range(120)]  # 10 m/s
    lines.append(line(120, 120 / 60, Speed=36.0, PositionX=5000.0, PositionZ=0.0))  # teleport
    lines += [line(121 + i, (121 + i) / 60, Speed=36.0, PositionX=5000.0 + i / 6, PositionZ=0.0) for i in range(60)]
    lines += [empty_line(181 + i, (181 + i) / 60) for i in range(10)]
    lines += [line(191 + i, (191 + i) / 60, Speed=36.0, PositionX=6000.0 + i / 6, PositionZ=0.0) for i in range(60)]
    r = lab.route(cols(lines), 0, 10, min_step_m=0.0)
    assert len(r["lines"]) == 3 and r["jumps"] == 1
    assert r["lines"][1][0][0] == 5000.0


def test_power_curve_uses_full_throttle_forward_gears_only():
    lines = []
    for i in range(400):
        rpm = 3000 + (i % 200) * 20
        full = i < 200
        lines.append(line(i, i / 60, CurrentEngineRpm=float(rpm), Power=rpm * 50.0, Torque=400.0, Accel=255 if full else 100, Gear=3))
    lines.append(line(400, 400 / 60, CurrentEngineRpm=5000.0, Power=1e9, Accel=255, Gear=0))  # reverse: excluded
    pc = lab.power_curve(cols(lines), 0, 10, bin_rpm=500)
    assert pc["samples_used"] == 200
    b = next(x for x in pc["bins"] if x["rpm_from"] == 5000)
    assert 5000 * 50 / 1000 <= b["power_kw_median"] <= 5500 * 50 / 1000
    assert "not a dyno" in pc["note"]


# ------------------------------------------------------------------ events, export
def test_events_have_rules_and_confidence():
    lines = []
    for i in range(240):  # 4 s; 1..2 s wheelspin under throttle
        spin = 60 <= i < 120
        lines.append(line(i, i / 60, Speed=30.0, Accel=255, TireCombinedSlipRearLeft=1.8 if spin else 0.2, SmashableVelDiff=2.0 if i == 200 else 0.0, SmashableMass=0.2))
    lines += [line(240 + i, 6 + i / 60, Speed=30.0) for i in range(10)]  # 2 s data gap
    ev = lab.detect_events(cols(lines))
    types = {e["type"]: e for e in ev}
    assert types["grip_loss_on_throttle"]["t"] == pytest.approx(1.0) and "RL" in types["grip_loss_on_throttle"]["label"]
    assert types["possible_smashable_hit"]["confidence"] == "heuristic"
    assert types["data_gap"]["t_end"] == pytest.approx(6.0)
    assert all(e["rule"] for e in ev)


def test_csv_export_full_resolution():
    lines = [line(i, i / 60, Speed=10.0) for i in range(120)] + [empty_line(120, 2.0)]
    rows = list(lab.export_rows(cols(lines), ["speed", "throttle"], 0, 5))
    assert rows[0].startswith("sample_index,recording_s,game_timestamp_ms,segment,driving_data,speed_km_per_h,throttle_%")
    assert len(rows) == 122
    assert rows[1].split(",")[5] == "36" and rows[-1].strip().endswith(",0,,")


def test_cache_rebuilds_when_recording_grows():
    c = lab.LabCache(max_items=2)
    built = []
    mk = lambda n: (lambda: built.append(n) or cols([line(i, i / 60) for i in range(n)]))  # noqa: E731
    assert len(c.get("r", 10, mk(10))) == 10
    assert len(c.get("r", 10, mk(10))) == 10
    assert len(c.get("r", 20, mk(20))) == 20
    assert built == [10, 20]
