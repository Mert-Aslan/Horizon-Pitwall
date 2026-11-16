"""Telemetry Lab: channel registry, columnar cache and range analyses for recordings.

Speed matters here (an hour is ~216 000 samples), so channels are computed straight from
the unpacked datagram tuple instead of building the full ``normalize`` dictionaries. The
rules are the same as ``decode.normalize`` (finite check, byte fractions, steer clamp,
empty packets -> no data) and ``tests/test_lab.py`` checks every channel against
``normalize`` on the real-game fixtures.

Facts from the first real session (16 Nov 2026) that shape the channel notes:
  * AccelerationY is ~0 when the car stands still: the accelerations do not include
    gravity (kinematic acceleration in car-local axes).
  * AccelerationX correlates with speed x yaw rate (r = 0.95): lateral axis as documented.
  * DistanceTraveled went negative (-1118 m) during free roam: it is not used; distance is
    integrated from speed over gap-free consecutive samples.

Every number that is a heuristic (event thresholds, power-curve filter) says so in its
output. Nothing here claims dyno accuracy, ideal temperatures or tuning advice.
"""

from __future__ import annotations

import base64
import math
import statistics
import threading
from array import array
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from ..telemetry import schema

G0 = 9.80665
NAN = float("nan")
GAP_S = 0.25  # consecutive samples further apart = a gap (same as the recorder summary)
_IX = {f.name: i for i, f in enumerate(schema.FIELDS)}
_TS = _IX["TimestampMS"]
_RACE = _IX["IsRaceOn"]
_NONZERO_CHECK = [i for n, i in _IX.items() if n not in ("IsRaceOn", "TimestampMS")]
WHEELS = (("fl", "FrontLeft"), ("fr", "FrontRight"), ("rl", "RearLeft"), ("rr", "RearRight"))


def _fin(x) -> float:
    return x if isinstance(x, (int, float)) and math.isfinite(x) else NAN


def _scale(name: str, k: float) -> Callable[[tuple], float]:
    i = _IX[name]
    return lambda t: _fin(t[i]) * k


def _raw(name: str) -> Callable[[tuple], float]:
    i = _IX[name]
    return lambda t: _fin(t[i])


def _byte_pct(name: str) -> Callable[[tuple], float]:
    i = _IX[name]
    return lambda t: t[i] / 255 * 100


def _steer(t: tuple) -> float:
    return max(-1.0, min(1.0, t[_IX["Steer"]] / 127)) * 100


@dataclass(frozen=True)
class Channel:
    id: str
    label: str
    unit: str
    group: str
    fn: Callable[[tuple], float]
    note: str = ""


CHANNELS: tuple[Channel, ...] = (
    Channel("speed", "Speed", "km/h", "Driving", _scale("Speed", 3.6)),
    Channel("rpm", "Engine speed", "rpm", "Driving", _raw("CurrentEngineRpm")),
    Channel("gear", "Gear code", "", "Driving", _raw("Gear"), "raw code: 1-10 forward, 0 reverse and 11 between gears (observed, not documented)"),
    Channel("throttle", "Throttle", "%", "Inputs", _byte_pct("Accel")),
    Channel("brake", "Brake", "%", "Inputs", _byte_pct("Brake")),
    Channel("clutch", "Clutch", "%", "Inputs", _byte_pct("Clutch")),
    Channel("handbrake", "Handbrake", "%", "Inputs", _byte_pct("HandBrake")),
    Channel("steer", "Steering", "%", "Inputs", _steer, "negative = left; -128 clamped to -100 %"),
    Channel("power", "Power", "kW", "Engine", _scale("Power", 1e-3), "as reported by the game (mechanical); can be negative"),
    Channel("torque", "Torque", "N·m", "Engine", _raw("Torque")),
    Channel("boost", "Boost", "psi", "Engine", _raw("Boost"), "above atmospheric"),
    Channel("fuel", "Fuel", "%", "Engine", _scale("Fuel", 100)),
    Channel("g_lat", "Lateral acceleration", "g", "Motion", _scale("AccelerationX", 1 / G0), "car-local X (right +); excludes gravity (real data: ~0 at rest)"),
    Channel("g_long", "Longitudinal acceleration", "g", "Motion", _scale("AccelerationZ", 1 / G0), "car-local Z (forward +); braking is negative"),
    Channel("g_vert", "Vertical acceleration", "g", "Motion", _scale("AccelerationY", 1 / G0), "car-local Y (up +); excludes gravity"),
    Channel("yaw_rate", "Yaw rate", "deg/s", "Motion", _scale("AngularVelocityY", 180 / math.pi)),
    Channel("vel_long", "Forward velocity", "km/h", "Motion", _scale("VelocityZ", 3.6), "car-local Z; negative = moving backwards"),
    *(Channel(f"slip_{k}", f"Combined slip {k.upper()}", "", "Tyres", _raw(f"TireCombinedSlip{s}"), "|x| > 1 = grip lost (game documentation)") for k, s in WHEELS),
    *(Channel(f"susp_{k}", f"Suspension {k.upper()}", "%", "Suspension", _scale(f"NormalizedSuspensionTravel{s}", 100), "0 = fully extended, 100 = fully compressed") for k, s in WHEELS),
    *(Channel(f"temp_{k}", f"Tyre temperature {k.upper()}", "raw", "Tyres", _raw(f"TireTemp{s}"), "unit not documented by the game (shown unconverted)") for k, s in WHEELS),
    Channel("pos_x", "Position X", "m", "Position", _raw("PositionX"), "world coordinates of the game map, not GPS"),
    Channel("pos_z", "Position Z", "m", "Position", _raw("PositionZ"), "world coordinates of the game map, not GPS"),
    Channel("pos_y", "Altitude (Y)", "m", "Position", _raw("PositionY"), "world coordinates of the game map, not GPS"),
)
BY_ID = {c.id: c for c in CHANNELS}


def is_empty_tuple(t: tuple) -> bool:
    return t[_RACE] == 0 and not any(t[i] for i in _NONZERO_CHECK)


class Columns:
    """All channels of one recording as float arrays (NaN = no value)."""

    def __init__(self) -> None:
        self.el = array("d")
        self.i = array("q")
        self.seg = array("l")
        self.ts = array("q")
        self.empty = bytearray()
        self.ch: dict[str, array] = {c.id: array("d") for c in CHANNELS}
        self.smash_events: list[dict] = []  # SmashableVelDiff is not a channel; kept as events
        self.car = array("d")  # CarOrdinal / CarPerformanceIndex (identity, not plotted)
        self.pi = array("d")

    def __len__(self) -> int:
        return len(self.el)

    @classmethod
    def build(cls, lines: Iterable[dict]) -> "Columns":
        col = cls()
        unpack = schema.STRUCT.unpack
        end = schema.FIELDS_END
        fns = [(col.ch[c.id], c.fn) for c in CHANNELS]
        iv, im = _IX["SmashableVelDiff"], _IX["SmashableMass"]
        last_smash = -10.0
        for line in lines:
            data = base64.b64decode(line["d"])
            if len(data) != schema.DOCUMENTED_PACKET_LENGTH:
                continue
            t = unpack(data[:end])
            col.el.append(line["el"])
            col.i.append(line["i"])
            col.seg.append(line["seg"])
            col.ts.append(t[_TS])
            empty = is_empty_tuple(t)
            col.empty.append(1 if empty else 0)
            col.car.append(NAN if empty else float(t[_IX["CarOrdinal"]]))
            col.pi.append(NAN if empty else float(t[_IX["CarPerformanceIndex"]]))
            if empty:
                for arr, _ in fns:
                    arr.append(NAN)
            else:
                for arr, fn in fns:
                    arr.append(fn(t))
                v = t[iv]
                if v == v and v > SMASH_THRESHOLD_MPS and line["el"] - last_smash > 1.0:
                    col.smash_events.append(_smash_event(line["el"], v, _fin(t[im])))
                    last_smash = line["el"]
        return col

    def index_range(self, t_from: float, t_to: float) -> tuple[int, int]:
        """[lo, hi) indexes with t_from <= el <= t_to (binary search)."""
        el = self.el

        def first_ge(x: float) -> int:
            lo, hi = 0, len(el)
            while lo < hi:
                mid = (lo + hi) // 2
                if el[mid] < x:
                    lo = mid + 1
                else:
                    hi = mid
            return lo

        lo = first_ge(t_from)
        hi = first_ge(math.nextafter(t_to, math.inf))
        return lo, hi

    def breaks(self, k: int) -> bool:
        """True if sample k does not continue sample k-1 (gap, segment change, empty)."""
        return (k == 0 or self.el[k] - self.el[k - 1] > GAP_S or self.seg[k] != self.seg[k - 1]
                or self.empty[k] or self.empty[k - 1])


class LabCache:
    """Small LRU of built columns, keyed by (recording, samples) so a recording that is
    still growing is rebuilt when it has more samples."""

    def __init__(self, max_items: int = 3) -> None:
        self.max_items = max_items
        self._lock = threading.Lock()
        self._items: OrderedDict[tuple[str, int], Columns] = OrderedDict()

    def get(self, rec_id: str, samples: int, build: Callable[[], Columns]) -> Columns:
        key = (rec_id, samples)
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        col = build()
        with self._lock:
            for k in [k for k in self._items if k[0] == rec_id]:
                del self._items[k]
            self._items[key] = col
            while len(self._items) > self.max_items:
                self._items.popitem(last=False)
        return col


# ------------------------------------------------------------------ analyses
def _num(x: float) -> Optional[float]:
    return None if x != x else x  # NaN -> None


def _stats(vals: list[float]) -> dict:
    fin = [v for v in vals if v == v]
    if not fin:
        return {"min": None, "max": None, "mean": None, "valid": 0, "missing": len(vals)}
    return {"min": min(fin), "max": max(fin), "mean": sum(fin) / len(fin), "valid": len(fin), "missing": len(vals) - len(fin)}


def gaps(col: Columns, lo: int, hi: int, t_from: float, t_to: float, limit: int = 500) -> list[dict]:
    """Stretches without driving data inside [t_from, t_to]: missing samples (> GAP_S)
    and empty game packets. Merged, bounded."""
    out: list[dict] = []

    def add(a: float, b: float, kind: str) -> None:
        if out and out[-1]["kind"] == kind and a - out[-1]["to"] <= GAP_S:
            out[-1]["to"] = b
        elif len(out) < limit:
            out.append({"from": a, "to": b, "kind": kind})

    if lo < hi and col.el[lo] - t_from > GAP_S:
        add(t_from, col.el[lo], "no_samples")
    for k in range(lo, hi):
        if k > lo and col.el[k] - col.el[k - 1] > GAP_S:
            add(col.el[k - 1], col.el[k], "no_samples")
        if col.empty[k]:
            nxt = col.el[k + 1] if k + 1 < len(col) else col.el[k]
            add(col.el[k], min(nxt, t_to), "no_driving_data")
    if lo < hi and t_to - col.el[hi - 1] > GAP_S and hi == len(col):
        pass  # after the last sample the recording simply ends
    return out


def series(col: Columns, ids: list[str], t_from: float, t_to: float, points: int) -> dict:
    """Min/max envelope per time bucket (peaks survive decimation) plus exact statistics
    over every sample in the range (never computed from the decimated envelope)."""
    lo, hi = col.index_range(t_from, t_to)
    width = max((t_to - t_from) / points, 1e-9)
    out_ch = {}
    for cid in ids:
        arr = col.ch[cid]
        mn: list = [None] * points
        mx: list = [None] * points
        cnt = [0] * points
        vals = []
        for k in range(lo, hi):
            v = arr[k]
            vals.append(v)
            if v != v:
                continue
            b = min(points - 1, int((col.el[k] - t_from) / width))
            if cnt[b] == 0:
                mn[b] = mx[b] = v
            else:
                if v < mn[b]:
                    mn[b] = v
                if v > mx[b]:
                    mx[b] = v
            cnt[b] += 1
        c = BY_ID[cid]
        out_ch[cid] = {"label": c.label, "unit": c.unit, "note": c.note, "min": mn, "max": mx, "count": cnt, "stats": _stats(vals)}
    return {
        "from": t_from,
        "to": t_to,
        "bucket_s": width,
        "points": points,
        "samples_in_range": hi - lo,
        "channels": out_ch,
        "gaps": gaps(col, lo, hi, t_from, t_to),
    }


def summary(col: Columns, t_from: float, t_to: float) -> dict:
    lo, hi = col.index_range(t_from, t_to)
    sp = col.ch["speed"]
    thr = col.ch["throttle"]
    brk = col.ch["brake"]
    glat = col.ch["g_lat"]
    glong = col.ch["g_long"]
    slips = [col.ch[f"slip_{k}"] for k, _ in WHEELS]
    gear = col.ch["gear"]
    dist = 0.0
    covered = 0.0
    full_thr = braking = grip_lost = 0.0
    shifts = 0
    last_fwd_gear = None
    empty_n = 0
    for k in range(lo, hi):
        if col.empty[k]:
            empty_n += 1
        g = gear[k]
        if g == g and 1 <= g <= 10:
            if last_fwd_gear is not None and g != last_fwd_gear:
                shifts += 1
            last_fwd_gear = g
        if k == lo or col.breaks(k):
            continue
        dt = col.el[k] - col.el[k - 1]
        covered += dt
        v0, v1 = sp[k - 1], sp[k]
        if v0 == v0 and v1 == v1:
            dist += (v0 + v1) / 2 / 3.6 * dt  # trapezoid, m
        if thr[k] == thr[k] and thr[k] >= 95:
            full_thr += dt
        if brk[k] == brk[k] and brk[k] >= 10:
            braking += dt
        if sp[k] == sp[k] and sp[k] > 18 and any(s[k] == s[k] and abs(s[k]) > 1 for s in slips):
            grip_lost += dt
    spv = [sp[k] for k in range(lo, hi) if sp[k] == sp[k]]
    gl = [glat[k] for k in range(lo, hi) if glat[k] == glat[k]]
    gz = [glong[k] for k in range(lo, hi) if glong[k] == glong[k]]
    duration = (col.el[hi - 1] - col.el[lo]) if hi - lo > 1 else 0.0
    return {
        "from": t_from,
        "to": t_to,
        "samples": hi - lo,
        "samples_without_driving_data": empty_n,
        "duration_s": duration,
        "covered_s": covered,
        "distance_m": dist,
        "distance_note": "integrated from speed over gap-free samples (the game's DistanceTraveled is not used: it went negative in real data)",
        "speed_kmh": {"max": max(spv) if spv else None, "mean": (sum(spv) / len(spv)) if spv else None},
        "g_lat_abs_max": max((abs(x) for x in gl), default=None),
        "g_long_max": max(gz, default=None),
        "g_long_min": min(gz, default=None),
        "full_throttle_s": full_thr,
        "braking_s": braking,
        "grip_lost_s": grip_lost,
        "grip_lost_rule": "any wheel |combined slip| > 1 above 18 km/h",
        "gear_changes": shifts,
    }


def gg(col: Columns, t_from: float, t_to: float, max_points: int = 3000) -> dict:
    lo, hi = col.index_range(t_from, t_to)
    gl, gz = col.ch["g_lat"], col.ch["g_long"]
    idx = [k for k in range(lo, hi) if gl[k] == gl[k] and gz[k] == gz[k]]
    step = max(1, math.ceil(len(idx) / max_points))
    pts = [[round(gl[k], 4), round(gz[k], 4)] for k in idx[::step]]
    combined = [math.hypot(gl[k], gz[k]) for k in idx]
    return {
        "points": pts,
        "valid_samples": len(idx),
        "stride": step,
        "combined_g_max": max(combined, default=None),
        "combined_g_p95": sorted(combined)[int(0.95 * (len(combined) - 1))] if combined else None,
        "note": "every n-th sample is drawn (stride); maxima are computed from all samples. Accelerations exclude gravity.",
    }


def route(col: Columns, t_from: float, t_to: float, max_points: int = 8000, min_step_m: float = 1.0) -> dict:
    """World X/Z polylines, split at gaps, empty packets, segment changes and jumps that
    are too long for the speed (teleport / rewind)."""
    lo, hi = col.index_range(t_from, t_to)
    xs, zs, sp = col.ch["pos_x"], col.ch["pos_z"], col.ch["speed"]
    lines: list[list[list[float]]] = []
    cur: list[list[float]] = []
    jumps = 0
    n = 0
    last = None
    for k in range(lo, hi):
        x, z = xs[k], zs[k]
        if x != x or z != z:
            if cur:
                lines.append(cur)
            cur, last = [], None
            continue
        if last is not None:
            dt = col.el[k] - col.el[k - 1]
            v = max(sp[k] if sp[k] == sp[k] else 0.0, sp[k - 1] if sp[k - 1] == sp[k - 1] else 0.0) / 3.6
            d = math.hypot(x - last[0], z - last[1])
            if col.breaks(k) or d > 2 * v * dt + 5:
                if d > 2 * v * dt + 5 and not col.breaks(k):
                    jumps += 1
                if cur:
                    lines.append(cur)
                cur, last = [], None
            elif d < min_step_m and k < hi - 1:
                continue
        cur.append([round(x, 2), round(z, 2)])
        last = (x, z)
        n += 1
    if cur:
        lines.append(cur)
    total = sum(len(l) for l in lines)
    if total > max_points:  # thin evenly, keep line ends
        step = math.ceil(total / max_points)
        lines = [l[::step] + ([l[-1]] if (len(l) - 1) % step else []) for l in lines]
    allp = [p for l in lines for p in l]
    bounds = None if not allp else {"x_min": min(p[0] for p in allp), "x_max": max(p[0] for p in allp), "z_min": min(p[1] for p in allp), "z_max": max(p[1] for p in allp)}
    return {"lines": lines, "bounds": bounds, "jumps": jumps, "note": "game world X/Z in metres (top view), not GPS; split at gaps, empty packets and jumps"}


def power_curve(col: Columns, t_from: float, t_to: float, bin_rpm: int = 250) -> dict:
    lo, hi = col.index_range(t_from, t_to)
    thr, gear, rpm, pw, tq, clutch = (col.ch[c] for c in ("throttle", "gear", "rpm", "power", "torque", "clutch"))
    bins: dict[int, list[tuple[float, float]]] = {}
    used = 0
    for k in range(lo, hi):
        if not (thr[k] == thr[k] and thr[k] >= 95 and gear[k] == gear[k] and 1 <= gear[k] <= 10 and rpm[k] == rpm[k] and pw[k] == pw[k]):
            continue
        if clutch[k] == clutch[k] and clutch[k] > 5:
            continue
        b = int(rpm[k] // bin_rpm) * bin_rpm
        bins.setdefault(b, []).append((pw[k], tq[k]))
        used += 1
    out = []
    for b in sorted(bins):
        vals = bins[b]
        tqs = [t for _, t in vals if t == t]
        out.append({"rpm_from": b, "rpm_to": b + bin_rpm, "n": len(vals), "power_kw_median": statistics.median(p for p, _ in vals),
                    "torque_median": statistics.median(tqs) if tqs else None})
    return {
        "bins": out,
        "samples_used": used,
        "filter": "throttle >= 95 %, forward gear 1-10, clutch <= 5 %, valid rpm and power",
        "note": "median of in-game reported power per rpm bin under full throttle; not a dyno measurement. Bins with few samples are unreliable.",
    }


def detect_events(col: Columns) -> list[dict]:
    """Heuristic events over the whole recording, each with its rule and values."""
    ev: list[dict] = []
    n = len(col)
    # data gaps and empty stretches
    k = 1
    while k < n:
        dt = col.el[k] - col.el[k - 1]
        if dt > 0.5:
            ev.append({"type": "data_gap", "t": col.el[k - 1], "t_end": col.el[k], "label": f"No samples for {dt:.1f} s",
                       "rule": "consecutive samples > 0.5 s apart", "confidence": "certain"})
        k += 1
    k = 0
    while k < n:
        if col.empty[k]:
            j = k
            while j + 1 < n and col.empty[j + 1]:
                j += 1
            ev.append({"type": "no_driving_data", "t": col.el[k], "t_end": col.el[j], "label": f"Game sent empty packets ({col.el[j] - col.el[k]:.1f} s)",
                       "rule": "IsRaceOn = 0 and every value zero", "confidence": "certain"})
            k = j + 1
        else:
            k += 1
    # segment changes (source or mode) within the recording
    for k in range(1, n):
        if col.seg[k] != col.seg[k - 1]:
            ev.append({"type": "segment_change", "t": col.el[k], "t_end": col.el[k], "label": "New stream segment", "rule": "source or mode changed", "confidence": "certain"})
    # loss of grip under power (sustained)
    sp, thr = col.ch["speed"], col.ch["throttle"]
    slips = [(w, col.ch[f"slip_{w}"]) for w, _ in WHEELS]

    def run(pred: Callable[[int], bool], min_s: float, make: Callable[[int, int], dict]) -> None:
        k = 0
        while k < n:
            if pred(k):
                j = k
                while j + 1 < n and pred(j + 1) and col.el[j + 1] - col.el[j] <= GAP_S:
                    j += 1
                if col.el[j] - col.el[k] >= min_s:
                    ev.append(make(k, j))
                k = j + 1
            else:
                k += 1

    def spin(k: int) -> bool:
        return (thr[k] == thr[k] and thr[k] >= 50 and sp[k] == sp[k] and sp[k] > 18
                and any(s[k] == s[k] and abs(s[k]) > 1 for _, s in slips))

    def spin_ev(a: int, b: int) -> dict:
        peak = max((abs(s[k]), w) for k in range(a, b + 1) for w, s in slips if s[k] == s[k])
        return {"type": "grip_loss_on_throttle", "t": col.el[a], "t_end": col.el[b], "label": f"Grip lost under throttle {col.el[b] - col.el[a]:.1f} s (peak slip {peak[0]:.1f} {peak[1].upper()})",
                "rule": "throttle >= 50 %, speed > 18 km/h, any wheel |combined slip| > 1, for >= 0.5 s", "confidence": "heuristic"}

    run(spin, 0.5, spin_ev)
    ev.extend(col.smash_events)
    return sorted(ev, key=lambda e: e["t"])


SMASH_THRESHOLD_MPS = 0.5


def _smash_event(el: float, v: float, mass: float) -> dict:
    return {"type": "possible_smashable_hit", "t": el, "t_end": el,
            "label": f"Possible hit on a smashable object (speed change {v:.2f} m/s, mass {mass:.2f})",
            "rule": f"SmashableVelDiff > {SMASH_THRESHOLD_MPS} m/s, at most one per second (game field; mass unit not verified)",
            "confidence": "heuristic"}


def export_rows(col: Columns, ids: list[str], t_from: float, t_to: float) -> Iterable[str]:
    lo, hi = col.index_range(t_from, t_to)
    head = ["sample_index", "recording_s", "game_timestamp_ms", "segment", "driving_data"] + [
        f"{c}_{BY_ID[c].unit or 'code'}".replace("/", "_per_").replace("·", "") for c in ids]
    yield ",".join(head) + "\n"
    cols = [col.ch[c] for c in ids]
    for k in range(lo, hi):
        vals = [col.i[k], f"{col.el[k]:.4f}", col.ts[k], col.seg[k], 0 if col.empty[k] else 1]
        vals += ["" if a[k] != a[k] else f"{a[k]:.6g}" for a in cols]
        yield ",".join(map(str, vals)) + "\n"


def channel_meta(col: Columns) -> list[dict]:
    n = len(col)
    out = []
    for c in CHANNELS:
        arr = col.ch[c.id]
        valid = sum(1 for v in arr if v == v)
        out.append({"id": c.id, "label": c.label, "unit": c.unit, "group": c.group, "note": c.note,
                    "valid": valid, "valid_fraction": (valid / n) if n else None})
    return out


def perf_samples(col: Columns):
    """Columns -> perf.Sample stream (offline run detection)."""
    from .perf import Sample

    sp, vl, thr, brk, gear, xs, zs = (col.ch[c] for c in ("speed", "vel_long", "throttle", "brake", "gear", "pos_x", "pos_z"))
    for k in range(len(col)):
        yield Sample(t=col.el[k], speed=sp[k], vel_long=vl[k], throttle=thr[k], brake=brk[k], gear=gear[k], empty=bool(col.empty[k]),
                     seg=col.seg[k], car=col.car[k], pi=col.pi[k], x=xs[k], z=zs[k], ts=col.ts[k], i=col.i[k])
