"""Decoding (bytes -> raw), validation and normalisation (raw -> normalized/derived/quality).

Layers (see docs/ARCHITECTURE.md):
  raw         exactly what the packet says, full precision; non-finite floats become
              None in JSON and are flagged in quality.
  normalized  SI units / fractions / enums, validated. Missing or invalid -> None plus a
              per-field reason. A measured zero stays 0.0.
  derived     values computed from normalized ones (km/h, kW, rpm fraction, g...). A
              derived value is None whenever any input is None/invalid.
Display rounding happens only in the UI.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional

from . import schema

STANDARD_GRAVITY = 9.80665
WATTS_PER_MECHANICAL_HP = 745.69987158227022


@dataclass
class DecodeResult:
    ok: bool
    length: int
    raw: dict[str, Any] = field(default_factory=dict)
    tail: bytes = b""
    reject_reason: Optional[str] = None


def decode(data: bytes) -> DecodeResult:
    n = len(data)
    if n not in schema.ACCEPTED_LENGTHS:
        if n == schema.FIELDS_END:
            reason = "length_323_undocumented"
        elif n < schema.FIELDS_END:
            reason = "too_short"
        else:
            reason = "unexpected_length"
        return DecodeResult(ok=False, length=n, reject_reason=reason)
    values = schema.STRUCT.unpack_from(data, 0)
    raw = {f.name: v for f, v in zip(schema.FIELDS, values)}
    return DecodeResult(ok=True, length=n, raw=raw, tail=bytes(data[schema.FIELDS_END:]))


# ---------------------------------------------------------------- normalisation


class _Q:
    """Collects per-field quality notes."""

    def __init__(self) -> None:
        self.fields: dict[str, dict[str, Any]] = {}
        self.flags: list[str] = []

    def mark(self, name: str, status: str, detail: Optional[str] = None) -> None:
        entry = {"status": status}
        if detail:
            entry["detail"] = detail
        self.fields[name] = entry


def _finite(raw: dict, name: str, q: _Q) -> Optional[float]:
    v = raw.get(name)
    if v is None:
        q.mark(name, "missing")
        return None
    if isinstance(v, float) and not math.isfinite(v):
        q.mark(name, "non_finite", repr(v))
        return None
    return float(v) if isinstance(v, float) else v


def _ranged(raw: dict, name: str, q: _Q) -> Optional[float]:
    """Finite value; out-of-documented-range values are kept but flagged."""
    v = _finite(raw, name, q)
    rng = schema.FIELD_BY_NAME[name].range
    if v is not None and rng is not None and not (rng[0] <= v <= rng[1]):
        q.mark(name, "out_of_range", f"{v} not in [{rng[0]}, {rng[1]}]")
    return v


def _flag01(raw: dict, name: str, q: _Q) -> Optional[bool]:
    v = _finite(raw, name, q)
    if v is None:
        return None
    if v in (0, 1):
        return bool(v)
    q.mark(name, "out_of_range", f"{v} not in {{0, 1}}")
    return None


def _byte_fraction(raw: dict, name: str, q: _Q) -> Optional[float]:
    v = _finite(raw, name, q)
    return None if v is None else v / 255.0


def _signed_fraction(raw: dict, name: str, q: _Q) -> Optional[float]:
    """S8 documented as -127..127 -> -1..1. -128 is representable but undocumented:
    flagged out_of_range and clamped to -1 in the normalized layer (raw keeps -128)."""
    v = _finite(raw, name, q)
    if v is None:
        return None
    if v < -127 or v > 127:
        q.mark(name, "out_of_range", f"{v} not in [-127, 127]")
    return max(-1.0, min(1.0, v / 127.0))


def _lap_time(raw: dict, name: str, q: _Q) -> Optional[float]:
    v = _finite(raw, name, q)
    if v is None:
        return None
    if v == 0.0:
        q.mark(name, "not_applicable", "0.0 means not applicable per documentation")
        return None
    return v


def _mul(a: Optional[float], k: float) -> Optional[float]:
    return None if a is None else a * k


# Gear codes outside 1..10 seen in real FH6 data (16 Nov 2026, one session, one AWD car):
# 0 on every sample moving backwards (107/107), 11 only in the middle of gear changes
# (3->11->2, 2->11->3 ...). Not documented by the game; labelled as observations.
OBSERVED_GEAR_CODES = {
    0: ("R", "reverse (code 0; observed, not documented)"),
    11: ("N", "between gears (code 11; observed during shifts, not documented)"),
}


def is_empty_packet(raw: dict[str, Any]) -> bool:
    """Real FH6 sends packets with IsRaceOn = 0 in which every other field except
    TimestampMS is zero (1418/1418 IsRaceOn = 0 packets in a 484 s recording). They carry
    no driving data; showing them as measured zeros (0 km/h, 0 rpm, max rpm 0) would be
    false. Detected from the bytes' meaning, not from IsRaceOn alone."""
    if raw.get("IsRaceOn") != 0:
        return False
    return all(v == 0 for k, v in raw.items() if k not in ("IsRaceOn", "TimestampMS"))


def _null_tree(d: dict) -> dict:
    return {k: (_null_tree(v) if isinstance(v, dict) else None) for k, v in d.items()}


def normalize(raw: dict[str, Any]) -> tuple[dict, dict, dict]:
    if is_empty_packet(raw):
        n, d, _ = _normalize(raw)
        n = _null_tree(n)
        n["race_on"] = False
        n["timestamp_ms"] = raw.get("TimestampMS")
        n["gear"]["verified"] = False
        return n, _null_tree(d), {"fields": {}, "invalid_field_count": 0, "flags": ["empty_packet"]}
    return _normalize(raw)


def _normalize(raw: dict[str, Any]) -> tuple[dict, dict, dict]:
    q = _Q()
    f = lambda n: _finite(raw, n, q)  # noqa: E731

    rpm = f("CurrentEngineRpm")
    rpm_max = f("EngineMaxRpm")
    rpm_idle = f("EngineIdleRpm")
    if rpm_max is not None and rpm_max <= 0:
        q.mark("EngineMaxRpm", "out_of_range", "max rpm <= 0")

    gear_raw = f("Gear")
    car_class = _ranged(raw, "CarClass", q)
    drivetrain = _ranged(raw, "DrivetrainType", q)

    wheels = {}
    for short, suffix in schema.WHEELS:
        wheels[short] = {
            "suspension_norm": _ranged(raw, f"NormalizedSuspensionTravel{suffix}", q),
            "suspension_m": f(f"SuspensionTravelMeters{suffix}"),
            "slip_ratio": f(f"TireSlipRatio{suffix}"),
            "slip_angle": f(f"TireSlipAngle{suffix}"),
            "combined_slip": f(f"TireCombinedSlip{suffix}"),
            "rotation_rad_s": f(f"WheelRotationSpeed{suffix}"),
            "on_rumble_strip": _flag01(raw, f"WheelOnRumbleStrip{suffix}", q),
            "in_puddle": _flag01(raw, f"WheelInPuddle{suffix}", q),
            "surface_rumble": f(f"SurfaceRumble{suffix}"),
            # Unit undocumented: value is passed through, never converted (P-4).
            "tire_temp": f(f"TireTemp{suffix}"),
        }

    normalized = {
        "race_on": _flag01(raw, "IsRaceOn", q),
        "timestamp_ms": raw.get("TimestampMS"),
        "speed_mps": f("Speed"),
        "rpm": rpm,
        "rpm_max": rpm_max,
        "rpm_idle": rpm_idle,
        "gear": {
            "raw": gear_raw,
            # Forward gears 1..10 by number; 0 and 11 from real observation (P-3).
            "label": (str(int(gear_raw)) if gear_raw is not None and 1 <= gear_raw <= 10
                      else OBSERVED_GEAR_CODES[int(gear_raw)][0] if gear_raw in OBSERVED_GEAR_CODES else None),
            "note": OBSERVED_GEAR_CODES[int(gear_raw)][1] if gear_raw in OBSERVED_GEAR_CODES else None,
            "verified": False,  # never documented by the game
        },
        "inputs": {
            "throttle": _byte_fraction(raw, "Accel", q),
            "brake": _byte_fraction(raw, "Brake", q),
            "clutch": _byte_fraction(raw, "Clutch", q),
            "handbrake": _byte_fraction(raw, "HandBrake", q),
            "steer": _signed_fraction(raw, "Steer", q),
        },
        "power_w": f("Power"),
        "torque_nm": f("Torque"),
        "boost_psi": f("Boost"),
        "fuel_fraction": _ranged(raw, "Fuel", q),
        "distance_m": f("DistanceTraveled"),
        "car": {
            "ordinal": f("CarOrdinal"),
            "class_code": car_class,
            "class_label": schema.CAR_CLASS_LABELS.get(car_class) if car_class is not None else None,
            "pi": _ranged(raw, "CarPerformanceIndex", q),
            "drivetrain_code": drivetrain,
            "drivetrain_label": schema.DRIVETRAIN_LABELS.get(drivetrain) if drivetrain is not None else None,
            "cylinders": f("NumCylinders"),
            "group": f("CarGroup"),
        },
        "wheels": wheels,
        "motion": {
            "accel_local_mps2": {"x": f("AccelerationX"), "y": f("AccelerationY"), "z": f("AccelerationZ")},
            "velocity_local_mps": {"x": f("VelocityX"), "y": f("VelocityY"), "z": f("VelocityZ")},
            "angular_velocity_rad_s": {"x": f("AngularVelocityX"), "y": f("AngularVelocityY"), "z": f("AngularVelocityZ")},
            "orientation_rad": {"yaw": f("Yaw"), "pitch": f("Pitch"), "roll": f("Roll")},
            "position_world_m": {"x": f("PositionX"), "y": f("PositionY"), "z": f("PositionZ")},
        },
        "race": {
            "lap_number": f("LapNumber"),
            "position": f("RacePosition"),
            "best_lap_s": _lap_time(raw, "BestLap", q),
            "last_lap_s": _lap_time(raw, "LastLap", q),
            "current_lap_s": _lap_time(raw, "CurrentLap", q),
            "race_time_s": f("CurrentRaceTime"),
        },
        "smashable": {"vel_diff_mps": f("SmashableVelDiff"), "mass_kg": f("SmashableMass")},
        "driving_line": _signed_fraction(raw, "NormalizedDrivingLine", q),
        "ai_brake_difference": _signed_fraction(raw, "NormalizedAIBrakeDifference", q),
    }

    rpm_fraction = None
    if rpm is not None and rpm_max is not None and rpm_max > 0:
        rpm_fraction = rpm / rpm_max
    acc = normalized["motion"]["accel_local_mps2"]
    power = normalized["power_w"]
    derived = {
        "speed_kmh": _mul(normalized["speed_mps"], 3.6),
        "speed_mph": _mul(normalized["speed_mps"], 2.2369362920544),
        "power_kw": _mul(power, 1e-3),
        "power_hp": None if power is None else power / WATTS_PER_MECHANICAL_HP,
        "rpm_fraction": rpm_fraction,  # not clamped; UI clamps for display
        "g_lateral": _mul(acc["x"], 1 / STANDARD_GRAVITY),
        "g_longitudinal": _mul(acc["z"], 1 / STANDARD_GRAVITY),
        "g_vertical": _mul(acc["y"], 1 / STANDARD_GRAVITY),
    }

    invalid = sum(1 for v in q.fields.values() if v["status"] in ("non_finite", "missing"))
    quality = {
        "fields": q.fields,
        "invalid_field_count": invalid,
        "flags": q.flags,
    }
    return normalized, derived, quality


def json_safe_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Raw layer for JSON: NaN/Inf -> None (the reason lives in quality.fields)."""
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in raw.items()}
