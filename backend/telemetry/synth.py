"""Synthetic packets for demo mode and fixtures. Everything produced here is fake.

Demo packets use CarOrdinal = DEMO_CAR_ORDINAL (negative, so it can never collide with a
real catalogue id). Demo packets enter the hub with origin="demo" and every frame carries
mode="demo"; the UI labels them permanently.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Optional

from . import schema

DEMO_CAR_ORDINAL = -1


def build_packet(values: Optional[dict[str, Any]] = None, *, length: int = schema.DOCUMENTED_PACKET_LENGTH, tail: bytes = b"\x00") -> bytes:
    """Pack named field values with the schema. Unspecified fields are zero.
    NOTE: this shares offsets with the decoder; tests verify critical offsets with
    independent ``struct.pack_into`` calls."""
    vals = values or {}
    ordered = [vals.get(f.name, 0.0 if f.is_float else 0) for f in schema.FIELDS]
    body = schema.STRUCT.pack(*ordered)
    data = body + tail
    if length >= len(data):
        return data + b"\x00" * (length - len(data))
    return data[:length]


_RATIOS = [3.6, 2.4, 1.75, 1.35, 1.08, 0.88]
_FINAL = 3.9
_WHEEL_R = 0.33


def demo_values(t: float) -> dict[str, Any]:
    """A deterministic 40 s loop: straight, braking zone, left corner, right corner."""
    period = 40.0
    p = (t % period) / period
    # speed profile (km/h)
    if p < 0.35:
        v = 80 + (p / 0.35) * 150
        throttle, brake = 1.0, 0.0
    elif p < 0.45:
        v = 230 - ((p - 0.35) / 0.10) * 120
        throttle, brake = 0.0, 0.85
    elif p < 0.70:
        v = 110 + ((p - 0.45) / 0.25) * 52
        throttle, brake = 0.72, 0.0
    elif p < 0.78:
        v = 162 - ((p - 0.70) / 0.08) * 60
        throttle, brake = 0.0, 0.55
    else:
        v = 102 - ((p - 0.78) / 0.22) * 22
        throttle, brake = 0.55, 0.0
    steer = 0.0
    if 0.45 <= p < 0.70:
        steer = -0.18 * math.sin((p - 0.45) / 0.25 * math.pi)
    elif 0.78 <= p:
        steer = 0.42 * math.sin((p - 0.78) / 0.22 * math.pi)
    v_ms = v / 3.6
    wheel_rad_s = v_ms / _WHEEL_R
    max_rpm, idle = 8000.0, 900.0
    gear = 1
    rpm = idle
    for g, r in enumerate(_RATIOS, start=1):
        rpm = wheel_rad_s * r * _FINAL * 60 / (2 * math.pi)
        gear = g
        if rpm < 7200:
            break
    rpm = max(idle, min(max_rpm, rpm))
    lat_g = steer * v_ms * 0.06
    long_g = 0.45 * throttle - 1.1 * brake
    temps = [86 + 10 * abs(steer) + 4 * brake + 0.5 * math.sin(t + i) for i in range(4)]
    slip = [0.05 + 0.6 * abs(steer) + (0.3 * brake if i < 2 else 0.2 * throttle) for i in range(4)]
    angle = 2 * math.pi * p
    vals: dict[str, Any] = {
        "IsRaceOn": 1,
        "TimestampMS": int(t * 1000) & 0xFFFFFFFF,
        "EngineMaxRpm": max_rpm,
        "EngineIdleRpm": idle,
        "CurrentEngineRpm": rpm,
        "AccelerationX": lat_g * 9.80665,
        "AccelerationY": 0.0,
        "AccelerationZ": long_g * 9.80665,
        "VelocityZ": v_ms,
        "Speed": v_ms,
        "Power": max(-40_000.0, throttle * rpm * 41.0 - brake * 20_000),
        "Torque": max(-80.0, throttle * 480.0 - brake * 60),
        "Boost": 14.0 * throttle,
        "Fuel": 0.62,
        "DistanceTraveled": t * 40.0,
        "CurrentRaceTime": t,
        "CarOrdinal": DEMO_CAR_ORDINAL,
        "CarClass": 3,
        "CarPerformanceIndex": 700,
        "DrivetrainType": 2,
        "NumCylinders": 6,
        "PositionX": 600 * math.cos(angle) + 120 * math.sin(3 * angle),
        "PositionY": 12.0,
        "PositionZ": 380 * math.sin(angle),
        "Accel": round(throttle * 255),
        "Brake": round(brake * 255),
        "Gear": gear,
        "Steer": round(steer * 127),
    }
    for i, (_, suffix) in enumerate(schema.WHEELS):
        load = 0.5 + 0.25 * (long_g if i >= 2 else -long_g) * 0.5 + 0.2 * (lat_g if i % 2 == 0 else -lat_g)
        vals[f"NormalizedSuspensionTravel{suffix}"] = max(0.05, min(0.95, load))
        vals[f"SuspensionTravelMeters{suffix}"] = 0.08 * max(0.05, min(0.95, load))
        vals[f"TireTemp{suffix}"] = temps[i]
        vals[f"TireCombinedSlip{suffix}"] = slip[i]
        vals[f"TireSlipRatio{suffix}"] = slip[i] * 0.6
        vals[f"TireSlipAngle{suffix}"] = slip[i] * 0.8
        vals[f"WheelRotationSpeed{suffix}"] = wheel_rad_s
    return vals


class DemoGenerator:
    """Feeds synthetic packets into the hub at ``hz`` while demo mode is on."""

    def __init__(self, hub, hz: float = 60.0) -> None:
        self.hub = hub
        self.hz = hz
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="demo", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        t0 = time.monotonic()
        period = 1.0 / self.hz
        nxt = t0
        while not self._stop.is_set():
            t = time.monotonic() - t0
            self.hub.ingest(build_packet(demo_values(t + 1.0)), "demo", origin="demo")
            nxt += period
            self._stop.wait(max(0.0, nxt - time.monotonic()))

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
