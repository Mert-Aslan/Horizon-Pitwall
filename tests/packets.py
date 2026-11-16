"""Independent packet writer for tests.

Offsets here are typed in by hand from the field order in the official document and the
documented type sizes, NOT imported from backend.telemetry.schema, so that a mistake in
the schema cannot be mirrored by the tests.
"""

import struct

OFF = {
    "IsRaceOn": (0, "<i"),
    "TimestampMS": (4, "<I"),
    "EngineMaxRpm": (8, "<f"),
    "EngineIdleRpm": (12, "<f"),
    "CurrentEngineRpm": (16, "<f"),
    "AccelerationX": (20, "<f"),
    "AccelerationZ": (28, "<f"),
    "NormalizedSuspensionTravelFrontLeft": (68, "<f"),
    "TireTempFrontLeft": (268, "<f"),
    "TireTempRearRight": (280, "<f"),
    "CarOrdinal": (212, "<i"),
    "CarClass": (216, "<i"),
    "CarPerformanceIndex": (220, "<i"),
    "DrivetrainType": (224, "<i"),
    "NumCylinders": (228, "<i"),
    "CarGroup": (232, "<I"),
    "SmashableVelDiff": (236, "<f"),
    "SmashableMass": (240, "<f"),
    "PositionX": (244, "<f"),
    "Speed": (256, "<f"),
    "Power": (260, "<f"),
    "Torque": (264, "<f"),
    "Fuel": (288, "<f"),
    "BestLap": (296, "<f"),
    "CurrentRaceTime": (308, "<f"),
    "LapNumber": (312, "<H"),
    "RacePosition": (314, "<B"),
    "Accel": (315, "<B"),
    "Brake": (316, "<B"),
    "Clutch": (317, "<B"),
    "HandBrake": (318, "<B"),
    "Gear": (319, "<B"),
    "Steer": (320, "<b"),
    "NormalizedDrivingLine": (321, "<b"),
    "NormalizedAIBrakeDifference": (322, "<b"),
}

# A plausible, entirely fake baseline. CarOrdinal is negative so it can never be
# catalogued as a real car.
BASE = {
    "IsRaceOn": 1,
    "TimestampMS": 10_000,
    "EngineMaxRpm": 8000.0,
    "EngineIdleRpm": 900.0,
    "CurrentEngineRpm": 6100.0,
    "CarOrdinal": -42,
    "CarClass": 3,
    "CarPerformanceIndex": 700,
    "DrivetrainType": 2,
    "NumCylinders": 6,
    "Speed": 45.0,
    "Accel": 184,
    "Gear": 4,
    "Steer": -23,
}


def packet(length: int = 324, **values) -> bytes:
    buf = bytearray(max(length, 324))
    merged = {**BASE, **values}
    for name, v in merged.items():
        off, fmt = OFF[name]
        struct.pack_into(fmt, buf, off, v)
    return bytes(buf[:length])
