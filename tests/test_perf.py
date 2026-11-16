"""Performance runs: interpolated crossings, distance, and every invalidation rule."""

import math

import pytest

from backend.analysis import perf
from backend.analysis.perf import LivePerf, RunDetector, Sample, detect_runs, keep_offline
from backend.telemetry.synth import build_packet

HZ = 60


def S(t, speed, **kw):
    base = dict(t=t, speed=speed, vel_long=speed, throttle=100.0, brake=0.0, gear=3.0, empty=False, seg=0, car=4210.0, pi=999.0,
                x=t * 10, z=0.0, ts=int(1000 + t * 1000))
    base.update(kw)
    if "x" not in kw:  # move consistently with the speed so the jump check stays quiet
        base["x"] = 0.0
    return Sample(**base)


def accel(rate_kmh_s, t_end, stand=1.0, **kw):
    """Standing for `stand` s, then constant acceleration."""
    out = []
    for k in range(int(t_end * HZ) + 1):
        t = k / HZ
        v = 0.0 if t <= stand else rate_kmh_s * (t - stand)
        out.append(S(t, v, **kw))
    return out


def run(samples, kind):
    d = RunDetector(kind)
    for s in samples:
        d.feed(s)
    return d.results


def test_0_100_time_and_distance_are_interpolated():
    res = run(accel(40.0, 5.0), "accel_0_100")  # 40 km/h per s
    assert len(res) == 1 and res[0].valid
    assert res[0].time_s == pytest.approx((100 - 1) / 40, abs=1e-6)  # timer starts at 1 km/h
    v1, v2, t = 1 / 3.6, 100 / 3.6, (100 - 1) / 40
    assert res[0].distance_m == pytest.approx((v1 + v2) / 2 * t, rel=1e-3)
    assert res[0].car_ordinal == 4210.0 and res[0].pi == 999.0


def test_no_start_is_invented_across_a_gap():
    ss = [s for s in accel(40.0, 5.0) if not (0.9 < s.t < 1.4)]  # missing samples around the launch
    assert run(ss, "accel_0_100") == []


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda s: Sample(**{**s.__dict__, "empty": True}) if 2.0 <= s.t < 2.05 else s, "no driving data"),
        (lambda s: Sample(**{**s.__dict__, "seg": 1}) if s.t >= 2.0 else s, "data stream changed"),
        (lambda s: Sample(**{**s.__dict__, "ts": 5}) if s.t >= 2.0 else s, "clock went back"),
        (lambda s: Sample(**{**s.__dict__, "car": 2010.0}) if s.t >= 2.0 else s, "car changed"),
        (lambda s: Sample(**{**s.__dict__, "x": 500.0}) if s.t >= 2.0 else s, "jumped"),
    ],
)
def test_data_problems_invalidate_and_are_always_reported(mutate, reason):
    res = run([mutate(s) for s in accel(40.0, 5.0)], "accel_0_100")
    assert len(res) == 1 and not res[0].valid and reason in res[0].reason
    assert res[0].reason_kind == "data" and keep_offline(res[0])


def test_gap_while_measuring():
    ss = [s for s in accel(40.0, 5.0) if not (2.0 < s.t < 2.5)]
    res = run(ss, "accel_0_100")
    assert not res[0].valid and "gap" in res[0].reason


def test_false_start_reported_only_if_it_got_going():
    def up_down(peak):
        ss = accel(40.0, 1 + peak / 40)
        t0 = ss[-1].t
        return ss + [S(t0 + k / HZ, max(0.0, peak - 40 * k / HZ)) for k in range(1, int(peak / 40 * HZ) + 3)]

    big = detect_runs(up_down(90))
    small = detect_runs(up_down(10))
    assert [r.reason for r in big if r.kind == "accel_0_100"] == ["stopped before 100 km/h (false start)"]
    assert [r for r in small if r.kind == "accel_0_100"] == []


def test_reversing_invalidates():
    ss = [Sample(**{**s.__dict__, "vel_long": -3.0}) if s.t > 2 else s for s in accel(40.0, 5.0)]
    assert "backwards" in run(ss, "accel_0_100")[0].reason


def test_100_200_rules():
    base = [S(k / HZ, 90 + 25 * k / HZ) for k in range(int(6 * HZ))]  # 90 -> 240 km/h
    ok = run(base, "accel_100_200")
    assert ok[0].valid and ok[0].time_s == pytest.approx(100 / 25, abs=1e-6)
    braked = run([Sample(**{**s.__dict__, "brake": 40.0}) if 2 < s.t < 2.1 else s for s in base], "accel_100_200")
    assert braked[0].reason == "brake used during the run"
    lifted = [S(k / HZ, 90 + 25 * k / HZ if k / HZ < 3 else 165 - 10 * (k / HZ - 3)) for k in range(int(6 * HZ))]
    assert run(lifted, "accel_100_200")[0].reason.startswith("speed dropped")


def test_brake_100_0():
    stop = [S(k / HZ, 130 - 50 * max(0, k / HZ - 0.5), brake=100.0 if k / HZ >= 0.5 else 0.0, throttle=0.0) for k in range(int(4 * HZ))]
    stop = [Sample(**{**s.__dict__, "speed": max(0.0, s.speed), "vel_long": max(0.0, s.speed)}) for s in stop]
    r = run(stop, "brake_100_0")
    assert r[0].valid and r[0].time_s == pytest.approx((100 - 1) / 50, abs=1e-6)
    v1, v2 = 100 / 3.6, 1 / 3.6
    assert r[0].distance_m == pytest.approx((v1 + v2) / 2 * (99 / 50), rel=1e-3)
    gassed = run([Sample(**{**s.__dict__, "throttle": 50.0}) if 1.5 < s.t < 1.6 else s for s in stop], "brake_100_0")
    assert gassed[0].reason == "throttle used while braking"


def test_corner_braking_is_not_reported_offline():
    # brake from 120 to 75 and accelerate again: ordinary driving, not a failed stop
    ss = [S(k / HZ, 120 - 30 * k / HZ, brake=80.0, throttle=0.0) for k in range(int(1.5 * HZ))]
    ss += [S(1.5 + k / HZ, 75 + 30 * k / HZ, throttle=100.0) for k in range(1, int(2 * HZ))]
    assert [r for r in detect_runs(ss) if r.kind == "brake_100_0"] == []


def test_live_armed_detector():
    lp = LivePerf()
    assert lp.status()["state"] == "idle"
    lp.arm("accel_0_100")
    pos = 0.0
    for k in range(int(5 * HZ)):
        t = 100 + k / HZ
        v = 0.0 if k < HZ else 40.0 * (k / HZ - 1)
        pos += v / 3.6 / HZ
        data = build_packet({"IsRaceOn": 1, "TimestampMS": 1000 + k * 16, "EngineMaxRpm": 8000.0, "CarOrdinal": -3, "CarPerformanceIndex": 700,
                             "Speed": v / 3.6, "VelocityZ": v / 3.6, "Accel": 255, "Gear": 2, "PositionX": pos})
        lp.offer({"data": data, "mono": t, "stream_segment_id": "s"})
        if k == 2 * HZ:
            mid = lp.status()
            assert mid["state"] == "measuring" and mid["elapsed_s"] == pytest.approx(1 - 1 / 40, abs=0.02)
    st = lp.status()
    assert st["state"] == "completed" and st["last"]["time_s"] == pytest.approx(99 / 40, abs=1e-3)
    assert st["history"][0]["valid"] is True
    lp.disarm()
    assert lp.status()["state"] == "idle"
    with pytest.raises(ValueError):
        lp.arm("0-60")


def test_kinds_are_documented():
    assert set(perf.KINDS) == {"accel_0_100", "accel_100_200", "brake_100_0"}
    assert math.isclose(perf.MAX_DT, 0.25)
