"""Single source of truth for the FH6 "Data Out" packet layout.

Source: Forza Support, "Forza Horizon 6 Data Out Documentation" (checked 2026-11-16).
The document lists the fields in order with their types and states a total packet size
of 324 bytes. Offsets are NOT given by the document; they are derived here by packing the
documented types back-to-back with no alignment padding. That derivation ends at byte
323, so the 324th byte is undocumented. It is treated as an opaque tail (see
docs/PROTOCOL.md, decision P-1). Byte order is not stated by the document; little-endian
is assumed (Windows/x64 game, matches every previous Forza title) and recorded as
assumption P-2.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

SCHEMA_ID = "fh6-dataout"
SCHEMA_VERSION = 1
BYTE_ORDER = "<"  # assumption P-2: little-endian
DOCUMENTED_PACKET_LENGTH = 324
# Lengths that are decoded into live telemetry. 323 (= fields only) is NOT accepted by
# default because the official document says 324; see decision P-1.
ACCEPTED_LENGTHS = frozenset({DOCUMENTED_PACKET_LENGTH})

_TYPE_CODES = {
    "S8": "b",
    "U8": "B",
    "U16": "H",
    "S32": "i",
    "U32": "I",
    "F32": "f",
}


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # documented type, e.g. "F32"
    unit: Optional[str]
    doc: str
    # Documented closed range (inclusive), if the document states one.
    range: Optional[tuple[float, float]] = None
    offset: int = -1

    @property
    def struct_code(self) -> str:
        return _TYPE_CODES[self.type]

    @property
    def size(self) -> int:
        return struct.calcsize(BYTE_ORDER + self.struct_code)

    @property
    def is_float(self) -> bool:
        return self.type == "F32"


def _w(prefix: str, type_: str, unit, doc, range_=None):
    return [Field(f"{prefix}{c}", type_, unit, doc, range_) for c in ("FrontLeft", "FrontRight", "RearLeft", "RearRight")]


_LOCAL = "car local space; X = right, Y = up, Z = forward"

_ORDERED: list[Field] = [
    Field("IsRaceOn", "S32", None, "1 when race is on, 0 in menus/race stopped", (0, 1)),
    Field("TimestampMS", "U32", "ms", "can overflow to 0 eventually"),
    Field("EngineMaxRpm", "F32", "rpm", "engine max rpm"),
    Field("EngineIdleRpm", "F32", "rpm", "engine idle rpm"),
    Field("CurrentEngineRpm", "F32", "rpm", "current engine rpm"),
    Field("AccelerationX", "F32", "m/s^2", _LOCAL),
    Field("AccelerationY", "F32", "m/s^2", _LOCAL),
    Field("AccelerationZ", "F32", "m/s^2", _LOCAL),
    Field("VelocityX", "F32", "m/s", _LOCAL),
    Field("VelocityY", "F32", "m/s", _LOCAL),
    Field("VelocityZ", "F32", "m/s", _LOCAL),
    Field("AngularVelocityX", "F32", "rad/s", "local space; X = pitch"),
    Field("AngularVelocityY", "F32", "rad/s", "local space; Y = yaw"),
    Field("AngularVelocityZ", "F32", "rad/s", "local space; Z = roll"),
    Field("Yaw", "F32", "rad", "car orientation"),
    Field("Pitch", "F32", "rad", "car orientation"),
    Field("Roll", "F32", "rad", "car orientation"),
    *_w("NormalizedSuspensionTravel", "F32", "fraction", "0 = max stretch, 1 = max compression", (0.0, 1.0)),
    *_w("TireSlipRatio", "F32", "normalized", "0 = full grip, |x| > 1 = loss of grip"),
    *_w("WheelRotationSpeed", "F32", "rad/s", "wheel rotation speed"),
    *_w("WheelOnRumbleStrip", "S32", None, "1 on rumble strip, 0 off", (0, 1)),
    *_w("WheelInPuddle", "S32", None, "1 in puddle, 0 not", (0, 1)),
    *_w("SurfaceRumble", "F32", "dimensionless", "force-feedback rumble value"),
    *_w("TireSlipAngle", "F32", "normalized", "0 = full grip, |x| > 1 = loss of grip"),
    *_w("TireCombinedSlip", "F32", "normalized", "0 = full grip, |x| > 1 = loss of grip"),
    *_w("SuspensionTravelMeters", "F32", "m", "actual suspension travel"),
    Field("CarOrdinal", "S32", None, "unique id of the car make/model"),
    Field("CarClass", "S32", None, "0 (D) .. 7 (X)", (0, 7)),
    Field("CarPerformanceIndex", "S32", None, "100 .. 999", (100, 999)),
    Field("DrivetrainType", "S32", None, "0 FWD, 1 RWD, 2 AWD", (0, 2)),
    Field("NumCylinders", "S32", None, "number of cylinders"),
    Field("CarGroup", "U32", None, "car group identifier"),
    Field("SmashableVelDiff", "F32", "m/s", "velocity loss from smashable collision"),
    Field("SmashableMass", "F32", "kg", "mass of recently hit smashable"),
    Field("PositionX", "F32", "m", "world space"),
    Field("PositionY", "F32", "m", "world space"),
    Field("PositionZ", "F32", "m", "world space"),
    Field("Speed", "F32", "m/s", "speed"),
    Field("Power", "F32", "W", "power"),
    Field("Torque", "F32", "N*m", "torque"),
    # The document says only "Tire temperature": the unit is NOT stated (open question P-4).
    *_w("TireTemp", "F32", None, "tire temperature, unit not documented"),
    Field("Boost", "F32", "psi", "boost above atmospheric"),
    Field("Fuel", "F32", "fraction", "0 empty .. 1 full", (0.0, 1.0)),
    Field("DistanceTraveled", "F32", "m", "total distance traveled"),
    Field("BestLap", "F32", "s", "0.0 if not applicable"),
    Field("LastLap", "F32", "s", "0.0 if not applicable"),
    Field("CurrentLap", "F32", "s", "0.0 if not applicable"),
    Field("CurrentRaceTime", "F32", "s", "seconds since driving started"),
    Field("LapNumber", "U16", None, "laps completed"),
    Field("RacePosition", "U8", None, "current race position"),
    Field("Accel", "U8", "0..255", "throttle input"),
    Field("Brake", "U8", "0..255", "brake input"),
    Field("Clutch", "U8", "0..255", "clutch input"),
    Field("HandBrake", "U8", "0..255", "handbrake input"),
    # "Current gear": encoding of reverse/neutral is NOT documented (open question P-3).
    Field("Gear", "U8", None, "current gear, R/N encoding undocumented"),
    Field("Steer", "S8", None, "-127 full left, 0 centre, 127 full right", (-127, 127)),
    Field("NormalizedDrivingLine", "S8", None, "-127 .. 127", (-127, 127)),
    Field("NormalizedAIBrakeDifference", "S8", None, "-127 .. 127", (-127, 127)),
]


def _assign_offsets(fields: list[Field]) -> tuple[Field, ...]:
    out = []
    offset = 0
    for f in fields:
        out.append(Field(f.name, f.type, f.unit, f.doc, f.range, offset))
        offset += f.size
    return tuple(out)


FIELDS: tuple[Field, ...] = _assign_offsets(_ORDERED)
FIELD_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}
FIELDS_END = FIELDS[-1].offset + FIELDS[-1].size  # 323
STRUCT = struct.Struct(BYTE_ORDER + "".join(f.struct_code for f in FIELDS))

assert STRUCT.size == FIELDS_END == 323, STRUCT.size
assert len(FIELDS) == 88

WHEELS = (("fl", "FrontLeft"), ("fr", "FrontRight"), ("rl", "RearLeft"), ("rr", "RearRight"))

# Enum tables. CarClass: the document names only 0 = D and 7 = X; the letters between
# are the long-standing Forza convention and are marked as such in capabilities.
CAR_CLASS_LABELS = {0: "D", 1: "C", 2: "B", 3: "A", 4: "S1", 5: "S2", 6: "R", 7: "X"}
CAR_CLASS_DOCUMENTED = {0, 7}
DRIVETRAIN_LABELS = {0: "FWD", 1: "RWD", 2: "AWD"}


def describe() -> dict:
    """Machine readable schema, served by /api/v1/capabilities."""
    return {
        "id": SCHEMA_ID,
        "version": SCHEMA_VERSION,
        "byte_order": "little-endian (assumed, not stated by the official document)",
        "documented_packet_length": DOCUMENTED_PACKET_LENGTH,
        "accepted_lengths": sorted(ACCEPTED_LENGTHS),
        "fields_end": FIELDS_END,
        "tail": {"offset": FIELDS_END, "length": DOCUMENTED_PACKET_LENGTH - FIELDS_END, "meaning": "undocumented, kept opaque"},
        "fields": [
            {"name": f.name, "type": f.type, "offset": f.offset, "unit": f.unit, "doc": f.doc, "range": list(f.range) if f.range else None}
            for f in FIELDS
        ],
    }
