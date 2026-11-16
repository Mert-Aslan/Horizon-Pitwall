r"""Manual session recorder.

State machine (server-side truth; the UI only mirrors it)::

    idle -> starting -> recording -> stopping -> completed
                    \-> failed           \-> failed

A new recording can be started from idle, completed or failed. ``start`` while a
recording is active and ``stop`` while none is active raise ``RecorderBusy`` /
``RecorderIdle`` (HTTP 409); the outcome is therefore deterministic for double clicks and
two clients. The recorder does not depend on any browser: closing the UI does not stop
a recording.

Data path: the hub calls ``offer`` / ``on_event`` under its lock; they only do a
``put_nowait`` into a bounded queue (overflow -> ``dropped`` counter). One writer thread
owns the SQLite connection and the chunk files; no compression or SQL happens on the
ingest path.

Chunk commit order: write ``chunk-N.jsonl.gz.partial`` -> close + fsync -> rename to
``chunk-N.jsonl.gz`` -> insert the chunk row and update counters in one transaction.
The filesystem and the database are not one transaction; ``reconcile`` (run at startup)
handles every crash point:
  * partial file, no row        -> complete lines are recovered into a final chunk
  * final file, no row          -> verified and registered
  * row, file missing           -> chunk marked ``missing``
  * recording still 'recording' -> becomes ``recovered`` (data kept) or ``incomplete``
Recordings are never deleted automatically.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import logging
import math
import os
import queue
import threading
import time
import uuid
import zlib
from pathlib import Path
from typing import Any, Callable, Optional

from ..telemetry import schema
from .store import CHUNK_FORMAT, Store, encode_line, read_chunk, recover_partial, sha256_file

log = logging.getLogger("pitwall.recorder")

DECODER_ID = f"{schema.SCHEMA_ID}/v{schema.SCHEMA_VERSION}"
CHUNK_MAX_SAMPLES = 1800  # ~30 s at 60 Hz
CHUNK_MAX_SECONDS = 30.0
SYNC_FLUSH_EVERY_S = 1.0
GAP_S = 0.25  # consecutive samples further apart than this count as a coverage gap
_STOP = object()


class RecorderBusy(RuntimeError):
    pass


class RecorderIdle(RuntimeError):
    pass


def _utc_iso(ts: Optional[float] = None) -> str:
    return _dt.datetime.fromtimestamp(ts if ts is not None else time.time(), _dt.timezone.utc).isoformat(timespec="milliseconds")


class _Summary:
    def __init__(self) -> None:
        self.speed_max: Optional[float] = None
        self.speed_sum = 0.0
        self.speed_n = 0
        self.cars: dict[str, int] = {}
        self.unverified_car_samples = 0
        self.empty_samples = 0
        self.gap_count = 0
        self.gap_time_s = 0.0
        self.last_el: Optional[float] = None
        self.first_el: Optional[float] = None

    def add(self, s: dict, el: float) -> None:
        if s.get("empty"):
            self.empty_samples += 1
        v = s.get("speed_kmh")
        if isinstance(v, (int, float)) and math.isfinite(v):
            self.speed_max = v if self.speed_max is None else max(self.speed_max, v)
            self.speed_sum += v
            self.speed_n += 1
        if s.get("car_status") == "confirmed" and s.get("car_ordinal") is not None:
            k = str(s["car_ordinal"])
            self.cars[k] = self.cars.get(k, 0) + 1
        else:
            self.unverified_car_samples += 1
        if self.first_el is None:
            self.first_el = el
        if self.last_el is not None and el - self.last_el > GAP_S:
            self.gap_count += 1
            self.gap_time_s += el - self.last_el
        self.last_el = el

    def as_dict(self, duration_s: float, samples: int) -> dict:
        covered = 0.0
        if samples and self.first_el is not None and self.last_el is not None:
            covered = max(0.0, self.last_el - self.first_el - self.gap_time_s)
        return {
            "speed_kmh_max": None if self.speed_max is None else round(self.speed_max, 1),
            "speed_kmh_mean": None if not self.speed_n else round(self.speed_sum / self.speed_n, 1),
            "cars_confirmed": self.cars,  # ordinal -> samples (identity confirmed by hysteresis)
            "samples_without_confirmed_car": self.unverified_car_samples,
            "empty_samples": self.empty_samples,  # game packets without driving data (IsRaceOn = 0, all zero)
            "gaps_over_250ms": self.gap_count,
            "gap_time_s": round(self.gap_time_s, 2),
            "covered_s": round(covered, 2),
            "coverage": None if duration_s <= 0 else round(min(1.0, covered / duration_s), 4),
        }


class Recorder:
    def __init__(self, store: Store, *, app_version: str, queue_size: int = 20_000,
                 chunk_max_samples: int = CHUNK_MAX_SAMPLES, chunk_max_seconds: float = CHUNK_MAX_SECONDS,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time,
                 open_chunk: Optional[Callable[[Path], Any]] = None) -> None:
        self.store = store
        self.app_version = app_version
        self.queue_size = queue_size
        self.chunk_max_samples = chunk_max_samples
        self.chunk_max_seconds = chunk_max_seconds
        self._clock, self._wall = clock, wall
        self._open_chunk = open_chunk or (lambda p: open(p, "wb"))
        self._lock = threading.Lock()
        self.state = "idle"
        self.rec_id: Optional[str] = None
        self.mode: Optional[str] = None
        self.error: Optional[str] = None
        self.last_result: Optional[dict] = None
        self._q: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._reset_counters()
        store.init()
        self.reconciled = self.reconcile()

    def _reset_counters(self) -> None:
        self.t0: Optional[float] = None
        self.samples = 0
        self.dropped = 0
        self.mode_mismatch = 0
        self.high_watermark = 0
        self.flush_ms_max = 0.0
        self.chunks_written = 0

    # ------------------------------------------------------------------ control
    def start(self, mode: str, title: Optional[str] = None) -> dict:
        with self._lock:
            if self.state in ("starting", "recording", "stopping"):
                raise RecorderBusy(self.state)
            self._reset_counters()
            self.state = "starting"
            self.error = None
            self.mode = mode
            self.rec_id = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
            self._q = queue.Queue(maxsize=self.queue_size)
            self._ready.clear()
            self._thread = threading.Thread(target=self._run, args=(self.rec_id, mode, title), name="recorder", daemon=True)
            self._thread.start()
        self._ready.wait(5.0)
        return self.status()

    def stop(self, reason: str = "user", timeout: float = 5.0) -> dict:
        with self._lock:
            if self.state not in ("starting", "recording"):
                raise RecorderIdle(self.state)
            self.state = "stopping"
            self._stop_reason = reason
            t = self._thread
        deadline = time.monotonic() + timeout
        self._put_control(_STOP, deadline)
        if t is not None:
            t.join(max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                # The writer is stuck (e.g. a hung disk). Whatever is still queued is lost.
                with self._lock:
                    self.error = f"writer did not finish within {timeout:.0f}s; {self._q.qsize()} queued samples not written"
                    log.error("event=recorder_stop_timeout recording=%s %s", self.rec_id, self.error)
        return self.status()

    def shutdown(self, timeout: float = 5.0) -> None:
        try:
            self.stop("app_shutdown", timeout)
        except RecorderIdle:
            pass

    def _put_control(self, item: Any, deadline: float) -> None:
        # The stop marker must get in even when the queue is full (offer() no longer
        # enqueues once the state is 'stopping', so the writer drains it) - but never
        # wait past the deadline if the writer is stuck.
        while time.monotonic() < deadline:
            try:
                self._q.put(item, timeout=0.2)
                return
            except queue.Full:
                if self._thread is None or not self._thread.is_alive():
                    return

    def status(self) -> dict:
        el = None if self.t0 is None or self.state not in ("recording", "stopping") else round(self._clock() - self.t0, 1)
        return {
            "state": self.state,
            "recording_id": self.rec_id,
            "mode": self.mode,
            "elapsed_s": el,
            "samples": self.samples,
            "dropped": self.dropped,
            "queue": self._q.qsize(),
            "queue_high_watermark": self.high_watermark,
            "error": self.error,
            "last_result": self.last_result,
        }

    # ------------------------------------------------------------ hub sinks
    def offer(self, sample: dict) -> None:
        if self.state != "recording":
            return
        if sample["mode"] != self.mode:
            self.mode_mismatch += 1
            return
        try:
            self._q.put_nowait(("s", sample))
        except queue.Full:
            self.dropped += 1
            return
        n = self._q.qsize()
        if n > self.high_watermark:
            self.high_watermark = n

    def on_event(self, ev: dict) -> None:
        if self.state != "recording":
            return
        try:
            self._q.put_nowait(("e", ev))
        except queue.Full:
            self.dropped += 1

    # ------------------------------------------------------------------ writer
    def _run(self, rec_id: str, mode: str, title: Optional[str]) -> None:
        conn = None
        w = None
        try:
            conn = self.store.writer()
            folder = self.store.rec_dir(rec_id)
            folder.mkdir(parents=True, exist_ok=False)
            with conn:
                conn.execute(
                    "INSERT INTO recordings (id, status, mode, title, started_utc, app_version, schema_version, decoder, chunk_format) VALUES (?,?,?,?,?,?,?,?,?)",
                    (rec_id, "recording", mode, title, _utc_iso(self._wall()), self.app_version, schema.SCHEMA_VERSION, DECODER_ID, CHUNK_FORMAT),
                )
            self.t0 = self._clock()
            w = _ChunkWriter(self, conn, rec_id, folder)
            with self._lock:
                if self.state == "starting":
                    self.state = "recording"
            log.info("event=recording_start recording=%s mode=%s", rec_id, mode)
        except Exception as e:  # noqa: BLE001
            self._fail(conn, rec_id, f"could not start recording: {e}")
            self._ready.set()
            if conn is not None:
                conn.close()
            return
        self._ready.set()

        try:
            while True:
                try:
                    item = self._q.get(timeout=0.5)
                except queue.Empty:
                    w.tick()
                    continue
                if item is _STOP:
                    break
                kind, payload = item
                if kind == "s":
                    w.add(payload)
                else:
                    w.event(payload)
            duration = round(self._clock() - self.t0, 3)
            w.finish()
            lost = self._q.qsize()
            summary = w.summary.as_dict(duration, w.next_index)
            with conn:
                conn.execute(
                    "UPDATE recordings SET status='completed', ended_utc=?, duration_s=?, samples=?, dropped=?, lost_on_stop=?, segments=?, stop_reason=?, summary_json=? WHERE id=?",
                    (_utc_iso(self._wall()), duration, w.next_index, self.dropped, lost, w.segment_index + 1, getattr(self, "_stop_reason", "user"), json.dumps(summary), rec_id),
                )
            with self._lock:
                self.state = "completed"
                self.last_result = {"recording_id": rec_id, "status": "completed", "samples": w.next_index, "dropped": self.dropped, "duration_s": duration}
            log.info("event=recording_stop recording=%s samples=%d dropped=%d", rec_id, w.next_index, self.dropped)
        except Exception as e:  # noqa: BLE001 - disk full, permission, ...
            pending = w.pending_count() if w else 0
            written = (w.next_index - pending) if w else 0
            self._fail(conn, rec_id, f"recording failed: {e}", samples=written, lost=self._q.qsize() + pending)
        finally:
            if conn is not None:
                conn.close()

    def _fail(self, conn, rec_id: str, msg: str, samples: int = 0, lost: int = 0) -> None:
        log.error("event=recording_failed recording=%s %s", rec_id, msg)
        with self._lock:
            self.state = "failed"
            self.error = msg
            self.last_result = {"recording_id": rec_id, "status": "failed", "error": msg, "samples_written": samples, "samples_lost": lost + self.dropped}
        if conn is not None:
            try:
                with conn:
                    conn.execute(
                        "UPDATE recordings SET status='failed', error=?, ended_utc=?, dropped=?, lost_on_stop=? WHERE id=?",
                        (msg, _utc_iso(self._wall()), self.dropped, lost, rec_id),
                    )
            except Exception:  # noqa: BLE001 - the DB may be on the same full disk
                log.exception("could not mark recording %s as failed", rec_id)
        # stop consuming: hub sinks see state != recording and return immediately
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                break

    # ------------------------------------------------------------- reconcile
    def reconcile(self) -> list[dict]:
        """Bring files and metadata back in line after a crash (see module docstring)."""
        report = []
        conn = self.store.writer()
        try:
            open_recs = [r[0] for r in conn.execute("SELECT id FROM recordings WHERE status IN ('recording','starting','stopping')")]
            for rec_id in open_recs:
                report.append(self._reconcile_one(conn, rec_id))
        finally:
            conn.close()
        for r in report:
            log.warning("event=recording_reconciled %s", r)
        return report

    def _reconcile_one(self, conn, rec_id: str) -> dict:
        folder = self.store.rec_dir(rec_id)
        known = {row[0]: row for row in conn.execute("SELECT file, status, samples FROM chunks WHERE recording_id=?", (rec_id,))}
        notes = []
        with conn:
            for name, row in known.items():
                if not (folder / name).exists():
                    conn.execute("UPDATE chunks SET status='missing' WHERE recording_id=? AND file=?", (rec_id, name))
                    notes.append(f"{name}: missing")
            next_index = conn.execute("SELECT COALESCE(MAX(chunk_index)+1, 0) FROM chunks WHERE recording_id=?", (rec_id,)).fetchone()[0]
            for p in sorted(folder.glob("chunk-*.jsonl.gz")) if folder.exists() else []:
                if p.name in known:
                    continue
                try:
                    lines = read_chunk(p)
                except (OSError, EOFError, ValueError, zlib.error):
                    notes.append(f"{p.name}: unreadable, left in place")
                    continue
                self._register(conn, rec_id, next_index, p, lines, "ok")
                next_index += 1
                notes.append(f"{p.name}: registered ({len(lines)} samples)")
            for p in sorted(folder.glob("chunk-*.jsonl.gz.partial")) if folder.exists() else []:
                raw_lines = recover_partial(p)
                kept = p.with_name(p.name + ".orig")
                p.replace(kept)  # keep the original bytes; never delete user data
                if raw_lines:
                    final = folder / p.name.replace(".partial", "")
                    with gzip.open(final, "wb") as f:
                        f.writelines(raw_lines)
                    lines = [json.loads(x) for x in raw_lines]
                    self._register(conn, rec_id, next_index, final, lines, "recovered")
                    next_index += 1
                notes.append(f"{p.name}: {len(raw_lines)} complete samples recovered; original kept as {kept.name}")
            total = conn.execute("SELECT COALESCE(SUM(samples),0), MAX(t_end_s) FROM chunks WHERE recording_id=? AND status!='missing'", (rec_id,)).fetchone()
            status = "recovered" if total[0] else "incomplete"
            conn.execute(
                "UPDATE recordings SET status=?, samples=?, duration_s=?, stop_reason='crash_recovered', error=? WHERE id=?",
                (status, total[0], total[1], "; ".join(notes) or "no chunk files found", rec_id),
            )
        return {"recording_id": rec_id, "status": status, "samples": total[0], "notes": notes}

    @staticmethod
    def _register(conn, rec_id: str, index: int, path: Path, lines: list[dict], status: str) -> None:
        conn.execute(
            "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rec_id, index, path.name, lines[0]["i"] if lines else None, lines[-1]["i"] if lines else None, len(lines),
             lines[0]["el"] if lines else 0.0, lines[-1]["el"] if lines else 0.0, path.stat().st_size, sha256_file(path), status),
        )


class _ChunkWriter:
    """Owned by the writer thread."""

    def __init__(self, rec: Recorder, conn, rec_id: str, folder: Path) -> None:
        self.rec, self.conn, self.rec_id, self.folder = rec, conn, rec_id, folder
        self.next_index = 0  # recording sample index
        self.segment_index = -1
        self.segment_id: Optional[str] = None
        self.chunk_index = 0
        self.summary = _Summary()
        self.last_el: Optional[float] = None
        self._fh = None
        self._gz = None
        self._chunk_first: Optional[int] = None
        self._chunk_n = 0
        self._chunk_t0 = 0.0
        self._last_sync = time.monotonic()

    def pending_count(self) -> int:
        return self._chunk_n

    def add(self, s: dict) -> None:
        el = s["mono"] - self.rec.t0
        if s["stream_segment_id"] != self.segment_id:
            self.segment_index += 1
            self.segment_id = s["stream_segment_id"]
            with self.conn:
                self.conn.execute(
                    "INSERT INTO segments VALUES (?,?,?,?,?,?)",
                    (self.rec_id, self.segment_index, self.segment_id, s["source"], self.next_index, round(el, 4)),
                )
        if self._gz is None:
            self._open(el)
        self._gz.write(encode_line(self.next_index, self.segment_index, s["segment_sequence"], s["game_timestamp_ms"], el, round(s["wall"] * 1000), s["data"]))
        self.summary.add(s, el)
        self.next_index += 1
        self._chunk_n += 1
        self.last_el = el
        self.rec.samples = self.next_index
        if self._chunk_n >= self.rec.chunk_max_samples or el - self._chunk_t0 >= self.rec.chunk_max_seconds:
            self._close_chunk()
        else:
            self.tick()

    def event(self, ev: dict) -> None:
        el = None if self.rec.t0 is None else round(self.rec._clock() - self.rec.t0, 4)
        with self.conn:
            self.conn.execute(
                "INSERT INTO events VALUES (?,?,?,?,?,?)",
                (self.rec_id, self.next_index - 1 if self.next_index else None, el, ev.get("type"), ev.get("detail"), ev.get("stream_segment_id")),
            )

    def tick(self) -> None:
        # Make data written so far recoverable after a crash (gzip sync flush ~1/s).
        if self._gz is not None and time.monotonic() - self._last_sync >= SYNC_FLUSH_EVERY_S:
            self._gz.flush(zlib.Z_SYNC_FLUSH)
            self._fh.flush()
            self._last_sync = time.monotonic()

    def _partial(self) -> Path:
        return self.folder / f"chunk-{self.chunk_index:05d}.jsonl.gz.partial"

    def _open(self, el: float) -> None:
        self._fh = self.rec._open_chunk(self._partial())
        self._gz = gzip.GzipFile(fileobj=self._fh, mode="wb", compresslevel=6)
        self._chunk_first = self.next_index
        self._chunk_n = 0
        self._chunk_t0 = el
        self._chunk_first_el = el

    def _close_chunk(self) -> None:
        if self._gz is None:
            return
        t_flush = time.perf_counter()
        self._gz.close()
        self._fh.flush()
        os.fsync(self._fh.fileno())
        self._fh.close()
        partial = self._partial()
        final = partial.with_name(partial.name.replace(".partial", ""))
        partial.replace(final)
        with self.conn:
            self.conn.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.rec_id, self.chunk_index, final.name, self._chunk_first, self.next_index - 1, self._chunk_n,
                 round(self._chunk_first_el, 4), round(self.last_el or 0.0, 4), final.stat().st_size, sha256_file(final), "ok"),
            )
            self.conn.execute("UPDATE recordings SET samples=?, dropped=?, segments=? WHERE id=?", (self.next_index, self.rec.dropped, self.segment_index + 1, self.rec_id))
        self.rec.flush_ms_max = max(self.rec.flush_ms_max, (time.perf_counter() - t_flush) * 1000)
        self.rec.chunks_written += 1
        self.chunk_index += 1
        self._gz = self._fh = None
        self._chunk_n = 0

    def finish(self) -> None:
        self._close_chunk()
