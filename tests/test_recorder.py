"""M2: recorder state machine, storage, failure handling and crash recovery.

The hub is fed with synthetic packets in-process; files go to pytest's tmp_path only."""

import gzip
import json
import threading
import time

import pytest

from backend.recording.recorder import Recorder, RecorderBusy, RecorderIdle
from backend.recording.store import Store, read_chunk, sha256_file
from backend.telemetry.hub import TelemetryHub

from .packets import packet


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.005)
    return False


class StepClock:
    """Monotonic test clock shared by hub and recorder: +1/60 s on every reading taken
    by the hub's ingest (so samples get realistic, distinct elapsed times)."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(tmp_path, clock=None, **kw):
    store = Store(tmp_path / "recordings")
    if clock is not None:
        kw["clock"] = clock
    rec = Recorder(store, app_version="test", **kw)
    hub = TelemetryHub(clock=clock) if clock is not None else TelemetryHub()
    hub.set_receiver_state("listening")
    hub.sample_sinks.append(rec.offer)
    hub.event_sinks.append(rec.on_event)
    return hub, rec, store


def push(hub, n, ts0=1000, src=("127.0.0.1", 5200), origin="live", **values):
    for i in range(n):
        hub.ingest(packet(TimestampMS=ts0 + i * 16, **values), src, origin=origin)


def test_start_record_stop_reopen_and_verify(tmp_path):
    hub, rec, store = make(tmp_path, chunk_max_samples=100)
    st = rec.start("live", "first run")
    assert st["state"] == "recording" and st["recording_id"]
    push(hub, 250, Speed=30.0)
    assert wait_for(lambda: rec.samples == 250)
    out = rec.stop()
    assert out["state"] == "completed" and out["last_result"]["samples"] == 250
    meta = store.get(out["recording_id"])
    assert meta["status"] == "completed" and meta["samples"] == 250 and meta["title"] == "first run"
    assert [c["samples"] for c in meta["chunks"]] == [100, 100, 50]
    assert meta["decoder"] == "fh6-dataout/v1" and meta["chunk_format"] == "pitwall-jsonl-v1"
    assert meta["summary"]["speed_kmh_max"] == pytest.approx(108.0)
    v = store.verify(out["recording_id"])
    assert v["ok"] and v["samples_in_files"] == 250
    # the stored datagram is the original bytes
    first = read_chunk(store.rec_dir(out["recording_id"]) / meta["chunks"][0]["file"])[0]
    import base64
    assert base64.b64decode(first["d"]) == packet(TimestampMS=1000, Speed=30.0)
    assert first["i"] == 0 and first["ts"] == 1000


def test_double_start_and_stop_are_deterministic(tmp_path):
    hub, rec, _ = make(tmp_path)
    rec.start("live")
    with pytest.raises(RecorderBusy):
        rec.start("live")
    rec.stop()
    with pytest.raises(RecorderIdle):
        rec.stop()
    rec.start("live")  # a new recording after completion is allowed
    assert rec.status()["state"] == "recording"
    rec.stop()


def test_concurrent_start_only_one_wins(tmp_path):
    _, rec, store = make(tmp_path)
    results = []

    def go():
        try:
            rec.start("live")
            results.append("ok")
        except RecorderBusy:
            results.append("busy")

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["busy"] * 4 + ["ok"]
    rec.stop()
    assert len(store.list()) == 1


def test_source_change_creates_segments_and_events(tmp_path):
    hub, rec, store = make(tmp_path)
    rec.start("live")
    push(hub, 30, src=("127.0.0.1", 5200))
    hub.last_valid_mono -= 5  # bound source silent for > 3 s
    push(hub, 30, ts0=90_000, src=("192.168.1.9", 5201))
    assert wait_for(lambda: rec.samples == 60)
    rid = rec.stop()["recording_id"]
    meta = store.get(rid)
    assert meta["segments"] == 2
    assert [s["first_sample"] for s in meta["segments_detail"]] == [0, 30]
    assert "source_switch" in [e["type"] for e in meta["events"]]


def test_demo_samples_never_enter_a_live_recording(tmp_path):
    hub, rec, store = make(tmp_path)
    rec.start("live")
    push(hub, 10)
    hub.set_mode("demo")
    push(hub, 10, ts0=5000, src="demo", origin="demo")
    rid = rec.stop()["recording_id"]
    assert store.get(rid)["samples"] == 10
    assert rec.mode_mismatch == 10


def test_queue_overflow_is_counted_not_blocking(tmp_path):
    hub, rec, store = make(tmp_path, queue_size=1000)
    gate = threading.Event()
    rec.start("live")
    orig = rec._q.get  # the queue of *this* recording (start() creates a new one)

    def slow_get(*a, **k):
        gate.wait(5)
        return orig(*a, **k)

    rec._q.get = slow_get  # writer stalls: the hub must keep running
    t0 = time.perf_counter()
    push(hub, 3000)
    assert time.perf_counter() - t0 < 2.0
    assert rec.dropped >= 1999 and rec.high_watermark == 1000
    gate.set()
    out = rec.stop()
    meta = store.get(out["recording_id"])
    assert meta["dropped"] == rec.dropped and meta["samples"] + meta["dropped"] == 3000


class _FullDisk:
    """File object that fails like a full disk after ``ok_bytes``."""

    def __init__(self, path, ok_bytes):
        self.f = open(path, "wb")
        self.left = ok_bytes

    def write(self, b):
        if len(b) > self.left:
            raise OSError(28, "No space left on device")
        self.left -= len(b)
        return self.f.write(b)

    def flush(self):
        self.f.flush()

    def fileno(self):
        return self.f.fileno()

    def close(self):
        self.f.close()


def test_disk_full_fails_recording_but_live_keeps_running(tmp_path):
    calls = {"n": 0}

    def opener(path):
        calls["n"] += 1
        return _FullDisk(path, 10**9 if calls["n"] == 1 else 50)

    hub, rec, store = make(tmp_path, chunk_max_samples=50, open_chunk=opener)
    rec.start("live")
    push(hub, 400)
    assert wait_for(lambda: rec.status()["state"] == "failed")
    st = rec.status()
    assert "No space left" in st["error"]
    assert st["last_result"]["samples_written"] == 50
    push(hub, 10, ts0=99_000)  # receiver/hub unaffected
    assert hub.status()["connection_state"] == "receiving"
    meta = store.get(st["recording_id"])
    assert meta["status"] == "failed" and len(meta["chunks"]) == 1
    with pytest.raises(RecorderIdle):
        rec.stop()


def test_recording_survives_without_any_ui_client(tmp_path):
    """The recorder has no notion of browsers: only start/stop change its state."""
    hub, rec, _ = make(tmp_path)
    rec.start("live")
    push(hub, 20)
    assert wait_for(lambda: rec.samples == 20)
    assert rec.status()["state"] == "recording"
    rec.stop()


# ------------------------------------------------------------------ crash recovery
def crash_layout(tmp_path):
    """Simulate a crash: a recording row left in 'recording', one finished chunk with a
    row, one finished chunk without a row (crash between rename and commit) and a
    truncated .partial chunk (crash mid-write)."""
    hub, rec, store = make(tmp_path, chunk_max_samples=40)
    rid = rec.start("live")["recording_id"]
    push(hub, 40)
    assert wait_for(lambda: len(store.get(rid)["chunks"]) == 1)
    # freeze the writer's view: kill the thread by stopping, then roll the DB back
    rec.stop()
    conn = store.writer()
    with conn:
        conn.execute("UPDATE recordings SET status='recording', ended_utc=NULL WHERE id=?", (rid,))
    folder = store.rec_dir(rid)
    # chunk 1 complete on disk, not registered
    lines = [json.dumps({"i": 40 + k, "seg": 0, "sq": 41 + k, "ts": 5000 + k, "el": 1.0 + k / 60, "w": 0, "d": ""}) + "\n" for k in range(10)]
    with gzip.open(folder / "chunk-00001.jsonl.gz", "wt") as f:
        f.writelines(lines)
    # chunk 2 truncated mid-stream
    import zlib
    comp = zlib.compressobj(6, zlib.DEFLATED, 31)
    body = "".join(json.dumps({"i": 50 + k, "seg": 0, "sq": 51 + k, "ts": 6000 + k, "el": 2.0 + k / 60, "w": 0, "d": ""}) + "\n" for k in range(7))
    data = comp.compress(body.encode()) + comp.flush(zlib.Z_SYNC_FLUSH)
    data += comp.compress(b'{"i": 57, "seg": 0, "sq')  # cut mid-line, no trailer
    (folder / "chunk-00002.jsonl.gz.partial").write_bytes(data)
    conn.close()
    return store, rid


def test_crash_recovery_reconciles_every_case(tmp_path):
    store, rid = crash_layout(tmp_path)
    rec2 = Recorder(store, app_version="test")  # startup runs reconcile()
    assert rec2.reconciled and rec2.reconciled[0]["recording_id"] == rid
    meta = store.get(rid)
    assert meta["status"] == "recovered"
    assert [c["status"] for c in meta["chunks"]] == ["ok", "ok", "recovered"]
    assert meta["samples"] == 40 + 10 + 7
    folder = store.rec_dir(rid)
    assert (folder / "chunk-00002.jsonl.gz.partial.orig").exists()  # original kept
    v = store.verify(rid)
    assert v["ok"], v
    # second startup: nothing left to reconcile
    assert Recorder(store, app_version="test").reconciled == []


def test_missing_chunk_file_is_reported(tmp_path):
    hub, rec, store = make(tmp_path, chunk_max_samples=10)
    rid = rec.start("live")["recording_id"]
    push(hub, 30)
    assert wait_for(lambda: rec.samples == 30)
    rec.stop()
    (store.rec_dir(rid) / "chunk-00001.jsonl.gz").unlink()
    v = store.verify(rid)
    assert not v["ok"] and any("missing" in p for p in v["problems"])


def test_checksum_mismatch_detected(tmp_path):
    hub, rec, store = make(tmp_path)
    rid = rec.start("live")["recording_id"]
    push(hub, 5)
    assert wait_for(lambda: rec.samples == 5)
    rec.stop()
    p = store.rec_dir(rid) / "chunk-00000.jsonl.gz"
    before = sha256_file(p)
    p.write_bytes(p.read_bytes() + b"x")
    assert sha256_file(p) != before
    assert "checksum mismatch" in store.verify(rid)["problems"][0]


def test_metadata_edit_and_restart_consistency(tmp_path):
    hub, rec, store = make(tmp_path)
    rid = rec.start("live")["recording_id"]
    push(hub, 12)
    assert wait_for(lambda: rec.samples == 12)
    rec.stop()
    assert store.update_metadata(rid, {"car_label": "Test car", "setup_label": "soft rear", "notes": "wet", "status": "x"})
    rec2 = Recorder(store, app_version="test")  # "restart"
    meta = rec2.store.get(rid)
    assert (meta["car_label"], meta["setup_label"], meta["notes"], meta["status"]) == ("Test car", "soft rear", "wet", "completed")
    assert not store.update_metadata("nope", {"title": "x"})


def test_time_range_query_opens_only_overlapping_chunks(tmp_path):
    clock = StepClock()
    hub, rec, store = make(tmp_path, clock=clock, chunk_max_samples=20)
    rid = rec.start("live")["recording_id"]
    for i in range(100):
        clock.t += 1 / 60
        hub.ingest(packet(TimestampMS=1000 + i * 16), ("127.0.0.1", 5200))
    assert wait_for(lambda: rec.samples == 100)
    rec.stop()
    meta = store.get(rid)
    mid = meta["chunks"][2]
    got = list(store.iter_lines(rid, mid["t_start_s"], mid["t_end_s"]))
    assert [x["i"] for x in got] == list(range(mid["first_sample"], mid["last_sample"] + 1))
