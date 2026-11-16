"""Performance runs: 0-100 km/h, 100-200 km/h and 100-0 km/h braking.

One streaming state machine per run type, used the same way for recordings (offline)
and for the live stream (armed by the user)::

    armed  --(start crossing between two reliable samples)-->  measuring
    measuring --(end crossing)--> completed
    measuring --(gap, empty packet, segment/car change, game-time going back,
                 position jump, reversing, rule broken, timeout)--> invalid

Times come from the recording/stream clock (monotonic seconds), never from counting
frames. Both threshold crossings are linearly interpolated between the two neighbouring
samples, and only if those samples are at most ``MAX_DT`` apart: no start is invented
from a sample before a gap. With ~16-32 ms between samples the interpolation error is
in the order of 0.01 s; results are shown with two decimals.

Rules (documented in docs/PROTOCOL.md "Performance runs"):
  0-100     armed while the car stands (<= 1 km/h); timer starts when speed crosses
            1 km/h upwards (no roll-out allowance), stops at 100 km/h.
  100-200   armed below 98 km/h; starts crossing 100 upwards, stops at 200; invalid if
            the brake is used (> 10 %) or speed drops more than 3 km/h below its peak.
  100-0     armed above 105 km/h; starts crossing 100 downwards with brake >= 30 %,
            stops crossing 1 km/h; invalid with throttle > 10 % or speed rising > 3 km/h.
Reversing = car-local forward velocity below -1 km/h (VelocityZ; forward is +Z, which
real data supports: AccelerationZ follows d(speed)/dt with r = 0.92).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

DETECTOR_VERSION = 1
MAX_DT = 0.25  # s: neighbours further apart are not reliable for a crossing
TIMEOUT_S = 60.0
STAND_KMH = 1.0

KINDS = {
    "accel_0_100": {"label": "0–100 km/h", "from": 0, "to": 100},
    "accel_100_200": {"label": "100–200 km/h", "from": 100, "to": 200},
    "brake_100_0": {"label": "100–0 km/h braking", "from": 100, "to": 0},
}


@dataclass
class Sample:
    t: float  # monotonic seconds (recording elapsed or live clock)
    speed: float  # km/h, NaN when unknown
    vel_long: float  # km/h, car-local forward velocity
    throttle: float  # %
    brake: float  # %
    gear: float
    empty: bool
    seg: int
    car: float
    pi: float
    x: float
    z: float
    ts: int  # game TimestampMS
    i: int = -1  # sample index (offline)


def _cross(a: Sample, b: Sample, thr: float) -> float:
    if b.speed == a.speed:
        return b.t
    f = (thr - a.speed) / (b.speed - a.speed)
    return a.t + max(0.0, min(1.0, f)) * (b.t - a.t)


@dataclass
class RunResult:
    kind: str
    valid: bool
    t_start: float
    t_end: float
    time_s: Optional[float]
    distance_m: Optional[float]
    reason: Optional[str]
    car_ordinal: Optional[float]
    pi: Optional[float]
    samples: int
    max_dt_s: float
    extreme_kmh: Optional[float] = None  # highest (accel) or lowest (brake) speed seen
    reason_kind: Optional[str] = None  # "data" (stream problem) or "driving" (a rule was broken)
    i_start: int = -1
    i_end: int = -1

    def as_dict(self) -> dict:
        d = dict(self.__dict__)
        d["label"] = KINDS[self.kind]["label"]
        return d


@dataclass
class RunDetector:
    kind: str
    auto_rearm: bool = True
    state: str = "armed"  # armed | ready | measuring | completed | invalid | idle
    prev: Optional[Sample] = None
    start: Optional[float] = None
    start_s: Optional[Sample] = None
    dist: float = 0.0
    n: int = 0
    max_dt: float = 0.0
    peak: float = 0.0
    car: Optional[float] = None
    results: list = field(default_factory=list)
    last: Optional[RunResult] = None

    def _reset(self) -> None:
        self.start = None
        self.start_s = None
        self.dist = 0.0
        self.n = 0
        self.max_dt = 0.0
        self.peak = 0.0
        self.car = None

    def _finish(self, s: Sample, valid: bool, end_t: float, reason: Optional[str], reason_kind: Optional[str] = None) -> None:
        st = self.start_s
        res = RunResult(
            kind=self.kind, valid=valid, t_start=self.start if self.start is not None else s.t, t_end=end_t,
            time_s=(end_t - self.start) if valid and self.start is not None else None,
            distance_m=self.dist if valid else None, reason=reason,
            car_ordinal=self.car, pi=None if st is None or st.pi != st.pi else st.pi, samples=self.n, max_dt_s=round(self.max_dt, 4),
            extreme_kmh=self.peak if st is not None else None, i_start=st.i if st else -1, i_end=s.i,
            reason_kind=None if valid else (reason_kind or "driving"),
        )
        self.results.append(res)
        self.last = res
        self._reset()
        self.state = "armed" if self.auto_rearm else ("completed" if valid else "invalid")

    def _break_reason(self, a: Sample, b: Sample) -> Optional[str]:
        dt = b.t - a.t
        if dt > MAX_DT:
            return f"gap in the data ({dt:.2f} s without samples)"
        if b.empty:
            return "the game sent no driving data (IsRaceOn = 0)"
        if b.seg != a.seg:
            return "the data stream changed (new source or mode)"
        if b.ts < a.ts:
            return "the game clock went back (rewind or restart)"
        if b.car == b.car and a.car == a.car and b.car != a.car:
            return "the car changed"
        if all(v == v for v in (a.x, a.z, b.x, b.z)):
            v = max(a.speed if a.speed == a.speed else 0, b.speed if b.speed == b.speed else 0) / 3.6
            if math.hypot(b.x - a.x, b.z - a.z) > 2 * v * dt + 5:
                return "the car jumped (rewind or teleport)"
        if b.vel_long == b.vel_long and b.vel_long < -1:
            return "the car moved backwards"
        if b.speed != b.speed:
            return "speed missing"
        return None

    def feed(self, s: Sample) -> Optional[RunResult]:
        a, self.prev = self.prev, s
        before = len(self.results)
        if self.state in ("idle", "completed", "invalid"):
            return None
        if a is None:
            return None
        if self.state == "measuring":
            reason = self._break_reason(a, s)
            if reason:
                self._finish(s, False, s.t, reason, "driving" if "backwards" in reason else "data")
            else:
                self._measure(a, s)
        else:
            if self._break_reason(a, s) is None:
                self._arm_or_start(a, s)
            elif self.state == "ready":
                self.state = "armed"
        return self.results[-1] if len(self.results) > before else None

    # ------------------------------------------------------------ per kind
    def _arm_or_start(self, a: Sample, b: Sample) -> None:
        k = self.kind
        if k == "accel_0_100":
            if self.state == "armed" and a.speed <= STAND_KMH and b.speed <= STAND_KMH:
                self.state = "ready"
            if self.state == "ready" and a.speed <= STAND_KMH < b.speed:
                self._begin(a, b, _cross(a, b, STAND_KMH))
        elif k == "accel_100_200":
            if self.state == "armed" and b.speed < 98:
                self.state = "ready"
            if self.state == "ready" and a.speed < 100 <= b.speed and b.brake <= 10:
                self._begin(a, b, _cross(a, b, 100))
        elif k == "brake_100_0":
            if self.state == "armed" and b.speed > 105:
                self.state = "ready"
            if self.state == "ready" and a.speed > 100 >= b.speed:
                if b.brake >= 30:
                    self._begin(a, b, _cross(a, b, 100))
                else:
                    self.state = "armed"  # slowed down without braking: wait for the next try

    def _begin(self, a: Sample, b: Sample, t0: float) -> None:
        self.state = "measuring"
        self.start = t0
        self.start_s = b
        self.car = b.car if b.car == b.car else None
        self.n = 1
        self.max_dt = b.t - a.t
        self.peak = b.speed
        # distance from the start crossing to b (speed linear between a and b)
        thr = KINDS[self.kind]["from"] if self.kind != "accel_0_100" else STAND_KMH
        self.dist = (thr + b.speed) / 2 / 3.6 * (b.t - t0)

    def _measure(self, a: Sample, b: Sample) -> None:
        self.n += 1
        dt = b.t - a.t
        self.max_dt = max(self.max_dt, dt)
        k = self.kind
        end_thr = KINDS[k]["to"] if k != "brake_100_0" else STAND_KMH
        crossed = (a.speed < end_thr <= b.speed) if k != "brake_100_0" else (a.speed > end_thr >= b.speed)
        if crossed:
            t1 = _cross(a, b, end_thr)
            self.dist += (a.speed + end_thr) / 2 / 3.6 * (t1 - a.t)
            self._finish(b, True, t1, None)
            return
        self.dist += (a.speed + b.speed) / 2 / 3.6 * dt
        if b.t - (self.start or b.t) > TIMEOUT_S:
            self._finish(b, False, b.t, f"took longer than {TIMEOUT_S:.0f} s")
            return
        if k == "accel_0_100":
            self.peak = max(self.peak, b.speed)
            if b.speed <= STAND_KMH:
                self._finish(b, False, b.t, "stopped before 100 km/h (false start)")
        elif k == "accel_100_200":
            self.peak = max(self.peak, b.speed)
            if b.brake > 10:
                self._finish(b, False, b.t, "brake used during the run")
            elif b.speed < self.peak - 3:
                self._finish(b, False, b.t, "speed dropped (lifted or hit something)")
        elif k == "brake_100_0":
            # self.peak holds the lowest speed so far for a braking run
            if b.throttle > 10:
                self._finish(b, False, b.t, "throttle used while braking")
            elif b.speed > self.peak + 3:
                self._finish(b, False, b.t, "speed rose during the stop")
            else:
                self.peak = min(self.peak, b.speed)


def detect_runs(samples) -> list[RunResult]:
    """All runs (valid and invalid) in a sequence of Samples, all three kinds."""
    dets = [RunDetector(k) for k in KINDS]
    for s in samples:
        for d in dets:
            d.feed(s)
    out = [r for d in dets for r in d.results if keep_offline(r)]
    return sorted(out, key=lambda r: r.t_start)


def progress(r: RunResult) -> float:
    """How far an attempt got towards its target speed (0..1)."""
    k = KINDS[r.kind]
    x = r.extreme_kmh or 0.0
    if r.kind == "brake_100_0":
        return max(0.0, min(1.0, (100 - x) / (100 - STAND_KMH)))
    return max(0.0, min(1.0, (x - k["from"]) / (k["to"] - k["from"])))


def keep_offline(r: RunResult) -> bool:
    """Recordings are scanned without the driver arming anything, so ordinary driving
    crosses these speeds all the time (every corner brakes through 100 km/h). A broken
    *driving* rule is only reported when the attempt got at least 80 % of the way; an
    attempt ended by a *data* problem is always reported (it may hide a real run)."""
    if r.valid or r.reason_kind == "data":
        return True
    return progress(r) >= 0.8


class LivePerf:
    """One user-armed detector on the live stream. ``offer`` is a hub sample sink (called
    under the hub lock): it only unpacks one datagram and steps the state machine."""

    def __init__(self, keep: int = 20) -> None:
        import threading
        from collections import deque

        self._lock = threading.Lock()
        self.det: Optional[RunDetector] = None
        self.history: deque = deque(maxlen=keep)
        self.armed_at: Optional[float] = None
        self._t: Optional[float] = None
        self._speed: Optional[float] = None

    def arm(self, kind: str) -> dict:
        if kind not in KINDS:
            raise ValueError(kind)
        with self._lock:
            self.det = RunDetector(kind, auto_rearm=False)
            self.armed_at = None
            self._t = None
        return self.status()

    def disarm(self) -> dict:
        with self._lock:
            self.det = None
        return self.status()

    def offer(self, sample: dict) -> None:
        det = self.det
        if det is None or det.state in ("completed", "invalid"):
            return
        from . import lab
        from ..telemetry import schema

        data = sample["data"]
        t = schema.STRUCT.unpack(data[: schema.FIELDS_END])
        empty = lab.is_empty_tuple(t)
        nan = float("nan")
        f = lab.BY_ID
        s = Sample(
            t=sample["mono"], speed=nan if empty else f["speed"].fn(t), vel_long=nan if empty else f["vel_long"].fn(t),
            throttle=nan if empty else f["throttle"].fn(t), brake=nan if empty else f["brake"].fn(t), gear=nan if empty else f["gear"].fn(t),
            empty=empty, seg=hash(sample["stream_segment_id"]) & 0xFFFFFFFF, car=nan if empty else float(t[lab._IX["CarOrdinal"]]),
            pi=nan if empty else float(t[lab._IX["CarPerformanceIndex"]]), x=nan if empty else f["pos_x"].fn(t), z=nan if empty else f["pos_z"].fn(t),
            ts=t[lab._IX["TimestampMS"]],
        )
        with self._lock:
            if self.det is not det:
                return
            res = det.feed(s)
            self._t = s.t
            self._speed = None if s.speed != s.speed else s.speed
            if res is not None:
                self.history.appendleft(res.as_dict())

    def status(self) -> dict:
        with self._lock:
            det = self.det
            measuring = det is not None and det.state == "measuring" and det.start is not None and self._t is not None
            return {
                "kind": det.kind if det else None,
                "label": KINDS[det.kind]["label"] if det else None,
                "state": det.state if det else "idle",
                "elapsed_s": round(self._t - det.start, 2) if measuring else None,
                "speed_kmh": self._speed,
                "last": det.last.as_dict() if det and det.last else None,
                "history": list(self.history)[:5],
            }
