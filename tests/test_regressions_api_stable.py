"""F-02 / F-04 / F-06 regressions written only against API that already existed in
0.2.0 (TelemetryHub, Config.validate, events), so the same file can be run against the
baseline commit to show it failed there (docs/IMPLEMENTATION_STATUS.md, "Fail-first")."""

import math

import pytest

from backend.config import Config
from backend.telemetry.hub import TelemetryHub

from .packets import packet


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_f02_history_does_not_mix_sources():
    c = Clock()
    h = TelemetryHub(clock=c)
    for i, v in enumerate((10.0, 11.0, 12.0)):
        c.t += 0.016
        h.ingest(packet(TimestampMS=100 + i * 16, Speed=v), ("127.0.0.1", 1))
    c.t += 3.5
    h.ingest(packet(TimestampMS=5000, Speed=90.0), ("10.0.0.2", 2))
    assert h.history_payload(60)["speed_kmh"] == pytest.approx([324.0])


@pytest.mark.parametrize("over", [{"lan": "false"}, {"stale_after_ms": math.nan}, {"udp_port": True}, {"web_port": 0}])
def test_f04_invalid_config_rejected(over):
    with pytest.raises(ValueError):
        Config(**over).validate()


def test_f06_transient_zero_ordinal_is_not_a_car_change():
    c = Clock()
    h = TelemetryHub(clock=c)
    ts = 100
    for o in [4156] * 30 + [0] * 60 + [4156] * 30:
        ts += 16
        c.t += 1 / 60
        h.ingest(packet(TimestampMS=ts, CarOrdinal=o), ("127.0.0.1", 1))
    assert [e for e in h.events if e["type"] == "car_change"] == []
