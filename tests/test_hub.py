import json

import pytest

from backend.telemetry.hub import TelemetryHub

from .packets import packet

SRC = ("127.0.0.1", 50001)
OTHER = ("192.168.1.20", 50002)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def hub(clock):
    h = TelemetryHub(stale_after_s=0.75, clock=clock)
    h.set_receiver_state("listening", address="127.0.0.1:0")
    return h


def feed(hub, clock, ts, src=SRC, dt=1 / 60, **kw):
    clock.advance(dt)
    return hub.ingest(packet(TimestampMS=ts, **kw), src)


def test_state_machine_waiting_receiving_stale(hub, clock):
    assert hub.status()["connection_state"] == "waiting"
    assert feed(hub, clock, 100) == "accepted"
    assert hub.status()["connection_state"] == "receiving"
    clock.advance(0.8)
    st = hub.status()
    assert st["connection_state"] == "stale"
    assert st["packet_age_ms"] == pytest.approx(800, abs=20)


def test_starting_and_error_states():
    h = TelemetryHub()
    assert h.status()["connection_state"] == "starting"
    h.set_receiver_state("error", error="port in use")
    st = h.status()
    assert st["connection_state"] == "error" and st["receiver_error"] == "port in use"


def test_invalid_spam_does_not_refresh_age(hub, clock):
    feed(hub, clock, 100)
    for _ in range(50):
        clock.advance(0.05)
        out = hub.ingest(packet(length=232), SRC)
        assert out.startswith("rejected")
    st = hub.status()
    assert st["connection_state"] == "stale"
    assert st["packet_age_ms"] >= 2400
    assert st["traffic_warning"] == "invalid_packets"
    assert hub.sequence == 1


def test_invalid_packet_never_replaces_latest_frame(hub, clock):
    feed(hub, clock, 100, Speed=10.0)
    clock.advance(0.01)
    hub.ingest(packet(length=323, Speed=99.0), SRC)
    assert hub.latest()["frame"]["normalized"]["speed_mps"] == 10.0


def test_duplicate_and_out_of_order_rejected(hub, clock):
    for ts in (100, 116, 133):
        assert feed(hub, clock, ts) == "accepted"
    assert feed(hub, clock, 133) == "duplicates"
    assert feed(hub, clock, 116) == "out_of_order"
    assert hub.latest()["frame"]["game_timestamp_ms"] == 133
    assert feed(hub, clock, 150) == "accepted"
    assert hub.counters["out_of_order"] == 1 and hub.counters["duplicates"] == 1


def test_wraparound_is_forward(hub, clock):
    feed(hub, clock, 0xFFFFFFF0)
    assert feed(hub, clock, 5) == "accepted"
    assert hub.counters["timestamp_wraparounds"] == 1


def test_single_backward_jump_does_not_reset(hub, clock):
    feed(hub, clock, 500_000)
    assert feed(hub, clock, 1_000) == "reset_pending"
    assert feed(hub, clock, 500_016) == "accepted"  # original stream continues
    assert hub.counters["timestamp_resets"] == 0


def test_reset_needs_consecutive_increasing_confirmation(hub, clock):
    feed(hub, clock, 500_000)
    session = hub.session_id
    assert feed(hub, clock, 1_000) == "reset_pending"
    assert feed(hub, clock, 1_016) == "reset_pending"
    assert feed(hub, clock, 1_033) == "accepted"
    assert hub.counters["timestamp_resets"] == 1
    assert hub.latest()["frame"]["game_timestamp_ms"] == 1_033
    assert hub.session_id == session  # a reset alone does not start a session


def test_foreign_source_rejected_then_switch_after_silence(hub, clock):
    feed(hub, clock, 100)
    first = hub.session_id
    assert feed(hub, clock, 200, src=OTHER) == "rejected:foreign_source"
    assert "192.168.1.20:50002" in hub.other_sources
    clock.advance(3.5)
    assert feed(hub, clock, 300, src=OTHER) == "accepted"
    assert hub.session_id != first
    assert hub.status()["source"] == "192.168.1.20:50002"


def test_demo_mode_ignores_live_packets_and_is_labelled(hub, clock):
    feed(hub, clock, 100)
    hub.set_mode("demo")
    assert hub.status()["connection_state"] == "waiting"
    assert feed(hub, clock, 200) == "ignored_mode"
    clock.advance(0.01)
    assert hub.ingest(packet(TimestampMS=1), "demo", origin="demo") == "accepted"
    assert hub.latest()["frame"]["mode"] == "demo"


def test_message_is_strict_json_and_raw_optional(hub, clock):
    import math

    feed(hub, clock, 100, Speed=math.nan)
    _, without = hub.message(include_raw=False)
    _, with_raw = hub.message(include_raw=True)
    a, b = json.loads(without), json.loads(with_raw)
    assert "raw" not in a["frame"] and b["frame"]["raw"]["Speed"] is None
    assert a["frame"]["quality"]["fields"]["Speed"]["status"] == "non_finite"
    assert a["status"]["connection_state"] == "receiving"
    for key in ("schema_version", "session_id", "sequence", "game_timestamp_ms", "received_at", "source", "normalized", "derived", "quality"):
        assert key in a["frame"]


def test_history_is_bounded(clock):
    h = TelemetryHub(history_seconds=1.0, clock=clock)
    for i in range(300):
        feed(h, clock, 100 + i * 16)
    assert len(h.history) <= 62
    hist = h.history_payload(30)
    assert len(hist["speed_kmh"]) == len(h.history)


def test_gap_estimation(hub, clock):
    ts = 100
    for _ in range(30):
        ts += 16
        feed(hub, clock, ts)
    ts += 160  # ~9 packets missing
    feed(hub, clock, ts)
    assert hub.counters["estimated_gaps"] == 1
    assert 7 <= hub.counters["estimated_missing_packets"] <= 10


def test_duplicate_analysis_separates_identical_from_changed(hub, clock):
    feed(hub, clock, 100, Speed=10.0)
    assert feed(hub, clock, 100, Speed=10.0) == "duplicates"  # same bytes again
    assert feed(hub, clock, 100, Speed=11.5) == "duplicates"  # new content, same timestamp
    assert feed(hub, clock, 100, Speed=12.0, IsRaceOn=0) == "duplicates"
    d = hub.diagnostics()["duplicate_analysis"]
    assert d["identical"] == 1 and d["changed"] == 2
    assert d["race_on"] == 2 and d["race_off"] == 1
    assert d["changed_fields_top"]["Speed"] == 2
    assert d["changed_samples"][0]["speed"] == [10.0, 11.5]
    # the latest frame is still the first accepted packet (policy unchanged)
    assert hub.latest()["frame"]["normalized"]["speed_mps"] == 10.0


def test_race_off_and_zero_ordinal_packets_are_counted(hub, clock):
    feed(hub, clock, 100, IsRaceOn=0, CarOrdinal=0)
    feed(hub, clock, 116)
    assert hub.counters["accepted_race_off"] == 1
    assert hub.counters["accepted_car_ordinal_zero"] == 1
