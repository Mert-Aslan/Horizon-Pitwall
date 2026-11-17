"""Stream identity, packet ordering and diagnostics tests."""

import math

import pytest

from backend.telemetry.caridentity import CarIdentity
from backend.telemetry.capture import classify_equal
from backend.telemetry.hub import TelemetryHub

from .packets import packet
from .test_hub import OTHER, SRC, Clock, feed


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def hub(clock):
    h = TelemetryHub(stale_after_s=0.75, clock=clock, server_instance_id="inst1")
    h.set_receiver_state("listening", address="127.0.0.1:0")
    return h


# Stream identity
def test_source_switch_starts_clean_segment_without_old_history(hub, clock):
    for i, v in enumerate((10.0, 11.0, 12.0)):  # 36 / 39.6 / 43.2 km/h
        feed(hub, clock, 100 + i * 16, Speed=v)
    seg_a = hub.stream_segment_id
    assert hub.history_payload(60)["speed_kmh"] == pytest.approx([36.0, 39.6, 43.2])
    clock.advance(3.5)
    assert feed(hub, clock, 5_000, src=OTHER, Speed=90.0) == "accepted"
    hist = hub.history_payload(60)
    assert hist["speed_kmh"] == pytest.approx([324.0])
    assert hist["stream_segment_id"] == hub.stream_segment_id != seg_a
    f = hub.latest()["frame"]
    assert f["sequence"] == 1  # sequence restarts inside the new segment
    assert f["server_instance_id"] == "inst1"
    assert f["session_id"] == f["stream_segment_id"]  # deprecated alias
    assert hub.status()["observed_hz"] is None  # rate estimate does not mix sources


def test_publish_seq_is_monotonic_across_segments(hub, clock):
    feed(hub, clock, 100)
    feed(hub, clock, 116)
    clock.advance(3.5)
    feed(hub, clock, 50, src=OTHER)
    assert hub.sequence == 1 and hub.publish_seq == 3


def test_frame_carries_three_clocks(hub, clock):
    feed(hub, clock, 1000)
    clock.advance(0.5)
    feed(hub, clock, 1500)
    f = hub.latest()["frame"]
    assert f["game_timestamp_ms"] == 1500
    assert f["segment_elapsed_ms"] == pytest.approx(500 + 1000 / 60, abs=1)
    assert isinstance(f["received_at"], int)


# Equal timestamps
def test_equal_timestamp_classification():
    a = packet(TimestampMS=5, Speed=10.0)
    assert classify_equal(a, a) == "identical"
    assert classify_equal(a, a[:323] + b"\x07") == "tail_only"
    assert classify_equal(a, packet(TimestampMS=5, Speed=10.5)) == "changed"
    assert classify_equal(packet(TimestampMS=5, Speed=math.nan), packet(TimestampMS=5, Speed=math.inf)) == "non_finite"
    assert classify_equal(packet(TimestampMS=5, Speed=math.nan), packet(TimestampMS=5, Speed=math.nan)) == "identical"


def test_default_policy_accepts_new_content_but_not_repeats(hub, clock):
    assert hub.equal_timestamp_policy == "accept_changed"
    feed(hub, clock, 100, Speed=25.0)
    assert feed(hub, clock, 100, Speed=25.0) == "duplicates"
    assert feed(hub, clock, 100, Speed=26.4) == "accepted"
    assert hub.latest()["frame"]["normalized"]["speed_mps"] == pytest.approx(26.4)
    d = hub.diagnostics()["duplicate_analysis"]
    assert d["identical"] == 1 and d["changed"] == 1 and d["accepted_under_policy"] == 1


def test_reject_policy_keeps_02_behaviour(clock):
    hub = TelemetryHub(clock=clock, equal_timestamp_policy="reject")
    feed(hub, clock, 100, Speed=25.0)
    assert feed(hub, clock, 100, Speed=26.4) == "duplicates"
    assert hub.latest()["frame"]["normalized"]["speed_mps"] == 25.0
    d = hub.diagnostics()["duplicate_analysis"]
    assert d["changed"] == 1 and d["accepted_under_policy"] == 0


def test_accept_changed_policy_keeps_identical_out_and_no_division_by_zero(clock):
    h = TelemetryHub(clock=clock, equal_timestamp_policy="accept_changed")
    h.set_receiver_state("listening")
    for ts in (100, 116, 133):
        feed(h, clock, ts)
    ema = h.diagnostics()["game_dt_ms_ema"]
    assert feed(h, clock, 133) == "duplicates"  # identical bytes: still a duplicate
    assert feed(h, clock, 133, Speed=50.0) == "accepted"  # new content, same ms
    assert h.latest()["frame"]["normalized"]["speed_mps"] == 50.0
    assert h.diagnostics()["game_dt_ms_ema"] == ema  # dt = 0 does not touch the estimate
    assert h.counters["estimated_gaps"] == 0
    seqs = [h.latest()["frame"]["sequence"]]
    assert seqs == [4]  # arrival order is kept by the app's own sequence


def test_tail_only_difference_is_not_new_content(clock):
    h = TelemetryHub(clock=clock, equal_timestamp_policy="accept_changed")
    a = packet(TimestampMS=100)
    clock.advance(0.016)
    h.ingest(a, SRC)
    clock.advance(0.016)
    assert h.ingest(a[:323] + b"\x01", SRC) == "duplicates"
    assert h.diagnostics()["duplicate_analysis"]["tail_only"] == 1


# Car identity
def run_ordinals(car: CarIdentity, seq, dt=1 / 60, t0=0.0):
    events, t = [], t0
    for o in seq:
        t += dt
        ev = car.update(o, t)
        if ev:
            events.append(ev)
    return events, t


def test_transient_zero_is_not_a_car_change():
    car = CarIdentity()
    ev, t = run_ordinals(car, [4156] * 30)
    assert ev == [("car_identified", "4156")] and car.status == "confirmed"
    ev, t = run_ordinals(car, [0] * 120, t0=t)  # 2 s of ordinal 0
    assert ev == [] and car.status == "temporarily_unavailable" and car.confirmed == 4156
    ev, _ = run_ordinals(car, [4156] * 5, t0=t)
    assert ev == [] and car.status == "confirmed"


def test_real_change_is_confirmed_once():
    car = CarIdentity()
    _, t = run_ordinals(car, [4156] * 30)
    ev, t = run_ordinals(car, [2010] * 5, t0=t)
    assert ev == [] and car.status == "candidate" and car.confirmed == 4156
    ev, _ = run_ordinals(car, [2010] * 40, t0=t)
    assert ev == [("car_change", "4156 -> 2010")] and car.confirmed == 2010


def test_flicker_between_cars_never_confirms():
    car = CarIdentity()
    _, t = run_ordinals(car, [4156] * 30)
    ev, _ = run_ordinals(car, [2010, 2010, 4156] * 30, t0=t)
    assert ev == [] and car.confirmed == 4156


def test_long_unknown_becomes_unavailable_once():
    car = CarIdentity(unavailable_after_s=10)
    _, t = run_ordinals(car, [4156] * 30)
    ev, _ = run_ordinals(car, [0] * (60 * 12), t0=t)
    assert len(ev) == 1 and ev[0][0] == "car_unavailable" and car.status == "unavailable"


def test_hub_events_for_zero_flicker(hub, clock):
    ts = 100
    for o in [4156] * 30 + [0] * 60 + [4156] * 30 + [0] * 30 + [4156] * 30:
        ts += 16
        feed(hub, clock, ts, CarOrdinal=o)
    types = [e["type"] for e in hub.events]
    assert types.count("car_change") == 0 and types.count("car_identified") == 1
    ci = hub.latest()["frame"]["car_identity"]
    assert ci == {"status": "confirmed", "confirmed_ordinal": 4156, "candidate_ordinal": None, "raw_ordinal": 4156, "live_verified": True}
    assert hub.counters["accepted_car_ordinal_zero"] == 90


# Diagnostic bounds
def test_other_sources_are_bounded_with_overflow_counter(hub, clock):
    feed(hub, clock, 100)
    for i in range(200):
        clock.advance(0.001)
        hub.ingest(packet(TimestampMS=200 + i), (f"10.0.{i // 250}.{i % 250}", 9000))
    assert len(hub.other_sources) == hub.other_sources_cap == 16
    assert hub.counters["other_sources_overflow"] == 200 - 16
    assert hub.counters["foreign_datagrams"] == 200


def test_other_sources_expire(clock):
    h = TelemetryHub(clock=clock, other_sources_cap=2, other_sources_ttl_s=10)
    feed(h, clock, 100)
    for i in range(2):
        h.ingest(packet(TimestampMS=200), (f"10.0.0.{i}", 1))
    h.last_valid_mono = clock() + 100  # keep the bound source "recent" for the test
    clock.advance(11)
    h.ingest(packet(TimestampMS=300), ("10.0.0.9", 1))
    assert set(h.other_sources) == {"10.0.0.9:1"}


def test_history_has_a_sample_cap(clock):
    h = TelemetryHub(clock=clock, history_seconds=120, history_max_samples=100)
    for i in range(500):
        feed(h, clock, 100 + i, dt=0.001)  # 1 kHz input: time window alone would keep 500
    assert len(h.history) == 100
