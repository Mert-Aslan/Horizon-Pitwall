"""TelemetryHub: the thread-safe heart of the live pipeline.

The UDP receiver thread (or the demo generator) calls ``ingest``. Everything else
(HTTP, WebSocket, recorder) reads snapshots or receives non-blocking callbacks. All ages
are computed from a monotonic clock on the server; the browser never compares its wall
clock with ours.

Identity (docs/ARCHITECTURE.md, "Identity and time"):
  * ``server_instance_id``  random per process start
  * ``stream_segment_id``   one continuous stream from one bound source in one mode. A new
                            segment starts on the first packet, after a source switch and
                            after a mode change. History, rate estimate, car identity and
                            ordering state are per segment. ``session_id`` in frames is a
                            deprecated alias of ``stream_segment_id`` (API v1 compatibility).
  * ``sequence``            1, 2, 3 ... within a segment (generated here, NOT by the game);
                            compare only together with (server_instance_id, stream_segment_id).
  * ``recording_session_id`` belongs to the recorder (backend/recording), not to the hub.

Time: every frame carries the raw ``game_timestamp_ms``, ``segment_elapsed_ms`` (process
monotonic time since the segment started; use this for intervals) and ``received_at``
(UTC wall clock in ms; for humans, never for intervals).

Ordering policy for TimestampMS (u32, "can overflow to 0 eventually"):
  * newer                -> accepted
  * equal                -> byte-identical / tail-only: ``duplicates`` (rejected). With new
                            content (real data: 217/217 such packets during driving had
                            28-49 changed fields) accepted under the default
                            "accept_changed" policy; "reject" restores 0.2 behaviour (D-1)
  * wraparound           -> accepted (previous near 2^32, new near 0)
  * slightly older       -> out-of-order datagram, rejected (counts as reorder)
  * much older           -> reset *candidate*; only after RESET_CONFIRM consecutive,
                            mutually increasing candidates is the reset accepted. A single
                            backwards timestamp never resets anything.
Arrival order is not proof of the game's simulation order; it is only what we received.

Source policy: the first sender is bound to the segment. Packets from other senders are
rejected (and listed, bounded, in diagnostics) until the bound sender has been silent for
``source_switch_after_s``; then the new sender starts a new segment.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import Counter, deque
from typing import Any, Callable, Optional

from . import decode as dec
from . import schema
from .capture import classify_equal, changed_fields
from .caridentity import CarIdentity

U32 = 1 << 32
WRAP_WINDOW_MS = 600_000
API_VERSION = 1

log = logging.getLogger("pitwall.hub")


def _addr_str(addr) -> str:
    if addr is None:
        return "unknown"
    if isinstance(addr, tuple):
        return f"{addr[0]}:{addr[1]}"
    return str(addr)


class _RateLimitedLog:
    """At most one log line per event type per ``every_s``; suppressed lines are counted."""

    def __init__(self, every_s: float = 5.0) -> None:
        self.every_s = every_s
        self._last: dict[str, float] = {}
        self._suppressed: Counter = Counter()

    def __call__(self, now: float, kind: str, msg: str, **ctx: Any) -> None:
        last = self._last.get(kind)
        if last is not None and now - last < self.every_s:
            self._suppressed[kind] += 1
            return
        self._last[kind] = now
        extra = self._suppressed.pop(kind, 0)
        ctx_s = " ".join(f"{k}={v}" for k, v in ctx.items() if v is not None)
        log.info("event=%s %s %s%s", kind, msg, ctx_s, f" (+{extra} similar suppressed)" if extra else "")


class TelemetryHub:
    def __init__(
        self,
        *,
        stale_after_s: float = 0.75,
        source_switch_after_s: float = 3.0,
        reorder_window_ms: int = 2000,
        reset_confirm: int = 3,
        history_seconds: float = 120.0,
        history_max_samples: int = 30_000,
        other_sources_cap: int = 16,
        other_sources_ttl_s: float = 600.0,
        equal_timestamp_policy: str = "accept_changed",
        server_instance_id: Optional[str] = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        if equal_timestamp_policy not in ("reject", "accept_changed"):
            raise ValueError(equal_timestamp_policy)
        self._lock = threading.Lock()
        self._clock = clock
        self._wall = wall
        self.stale_after_s = stale_after_s
        self.source_switch_after_s = source_switch_after_s
        self.reorder_window_ms = reorder_window_ms
        self.reset_confirm = reset_confirm
        self.history_seconds = history_seconds
        self.history_max_samples = history_max_samples
        self.other_sources_cap = other_sources_cap
        self.other_sources_ttl_s = other_sources_ttl_s
        self.equal_timestamp_policy = equal_timestamp_policy
        self.server_instance_id = server_instance_id or uuid.uuid4().hex[:12]
        self.started_at = clock()
        self.on_frame: Optional[Callable[[], None]] = None
        # Non-blocking callbacks, called under the hub lock (must only enqueue):
        self.sample_sinks: list[Callable[[dict], None]] = []
        self.event_sinks: list[Callable[[dict], None]] = []
        self.recorder_status: Optional[Callable[[], dict]] = None
        self.extra_status: dict[str, Callable[[], dict]] = {}  # e.g. live performance run state
        self.capture = None  # optional DiagnosticCapture (tap is called outside the lock)
        self._log_event = _RateLimitedLog()

        self.receiver_state = "starting"  # starting | listening | error | stopped
        self.receiver_error: Optional[str] = None
        self.receiver_address: Optional[str] = None
        self.mode = "live"  # live | demo

        self.counters: Counter = Counter()
        self.length_histogram: Counter = Counter()
        self.tail_values: Counter = Counter()
        self.reject_reasons: Counter = Counter()
        self.other_sources: dict[str, dict[str, Any]] = {}
        self.rejected_samples: deque = deque(maxlen=5)
        self.dup_changed_fields: Counter = Counter()  # bounded by the 88 field names
        self.dup_changed_samples: list[dict] = []
        self.events: deque = deque(maxlen=50)
        self.publish_seq = 0  # increments on every accepted frame, never resets (broadcaster)
        self.segment_count = 0
        self.car = CarIdentity()
        self._reset_mode_state()

    # ------------------------------------------------------------------ state
    def _reset_mode_state(self) -> None:
        self.stream_segment_id: Optional[str] = None
        self.bound_source: Optional[str] = None
        self.segment_started_mono: Optional[float] = None
        self.last_valid_mono: Optional[float] = None
        self.last_rejected_mono: Optional[float] = None
        self.last_rejected_reason: Optional[str] = None
        self.latest_frame: Optional[dict] = None
        self._latest_json: dict[bool, tuple[int, str]] = {}
        self._reset_segment_state()

    def _reset_segment_state(self) -> None:
        self.sequence = 0
        self.last_ts: Optional[int] = None
        self._reset_candidates: list[tuple[int, bytes, Any]] = []
        self._recv_times: deque = deque()
        self._dt_ema: Optional[float] = None
        self.history: deque = deque(maxlen=self.history_max_samples)
        self._last_bytes: Optional[bytes] = None
        self._last_raw: Optional[dict] = None
        self.car.reset()

    @property
    def session_id(self) -> Optional[str]:  # deprecated alias (API v1)
        return self.stream_segment_id

    def _event(self, kind: str, detail: str = "") -> None:
        ev = {"t_wall": self._wall(), "type": kind, "detail": detail, "stream_segment_id": self.stream_segment_id, "sequence": self.sequence}
        self.events.append(ev)
        self._log_event(self._clock(), kind, detail, segment=self.stream_segment_id, mode=self.mode)
        for sink in self.event_sinks:
            try:
                sink(ev)
            except Exception:  # noqa: BLE001 - a sink must never break ingest
                self.counters["sink_errors"] += 1

    def count(self, name: str, n: int = 1) -> None:
        with self._lock:
            self.counters[name] += n

    def set_receiver_state(self, state: str, *, error: Optional[str] = None, address: Optional[str] = None) -> None:
        with self._lock:
            self.receiver_state = state
            self.receiver_error = error
            if address:
                self.receiver_address = address
            self._event(f"receiver_{state}", error or address or "")

    def set_mode(self, mode: str) -> None:
        if mode not in ("live", "demo"):
            raise ValueError(mode)
        with self._lock:
            if mode == self.mode:
                return
            self.mode = mode
            self._reset_mode_state()
            self._event("mode", mode)

    # ------------------------------------------------------------------ ingest
    def ingest(self, data: bytes, addr: Any, origin: str = "live") -> str:
        outcome = self._ingest(data, addr, origin)
        cap = self.capture
        if cap is not None and origin == "live":
            cap.tap(data, _addr_str(addr), outcome)
        cb = self.on_frame
        if outcome == "accepted" and cb is not None:
            try:
                cb()  # wake the broadcaster; called outside the lock
            except Exception:  # noqa: BLE001 - a closed event loop must not break ingest
                pass
        return outcome

    def _ingest(self, data: bytes, addr: Any, origin: str) -> str:
        now = self._clock()
        src = _addr_str(addr)
        with self._lock:
            self.counters["received"] += 1
            self.length_histogram[len(data)] += 1
            if len(self.length_histogram) > 64:  # bound memory on garbage traffic
                self.length_histogram = Counter(dict(self.length_histogram.most_common(32)))
            if origin != self.mode:
                self.counters[f"ignored_{origin}_during_{self.mode}"] += 1
                return "ignored_mode"

            result = dec.decode(data)
            if not result.ok:
                return self._reject(now, result.reject_reason or "invalid", data, src)

            # --- source binding
            if self.bound_source is not None and src != self.bound_source:
                silent = self.last_valid_mono is None or now - self.last_valid_mono > self.source_switch_after_s
                if not silent:
                    self._note_other_source(src, now)
                    return self._reject(now, "foreign_source", data, src, sample=False)
                self._event("source_switch", f"{self.bound_source} -> {src}")
                self._start_segment(src, now)
            elif self.bound_source is None:
                self._start_segment(src, now)

            ts = result.raw["TimestampMS"]
            outcome = self._order(ts, now, data, src)
            if outcome == "reset_confirmed":
                # Replay the newest candidate as the accepted packet.
                ts, data, _ = self._reset_candidates[-1]
                self._reset_candidates.clear()
                result = dec.decode(data)
                self.counters["timestamp_resets"] += 1
                self._event("timestamp_reset", f"-> {ts}")
                self._dt_ema = None
            elif outcome == "duplicates":
                kind = self._analyse_duplicate(data, result.raw)
                if not (self.equal_timestamp_policy == "accept_changed" and kind in ("changed", "non_finite")):
                    self.counters["duplicates"] += 1
                    return "duplicates"
                self.counters["equal_timestamp_accepted"] += 1
            elif outcome != "accept":
                self.counters[outcome] += 1
                return outcome

            self._accept(result, ts, now, src, data)
            self._last_bytes = bytes(data)
            self._last_raw = result.raw
            return "accepted"

    def _note_other_source(self, src: str, now: float) -> None:
        info = self.other_sources.get(src)
        if info is None:
            # bounded: drop entries not seen for the TTL, then refuse new ones over the cap
            for k in [k for k, v in self.other_sources.items() if now - v["last_seen_mono"] > self.other_sources_ttl_s]:
                del self.other_sources[k]
            if len(self.other_sources) >= self.other_sources_cap:
                self.counters["other_sources_overflow"] += 1
                return
            info = self.other_sources[src] = {"packets": 0}
        info["packets"] += 1
        info["last_seen_mono"] = now
        info["last_seen_wall"] = self._wall()

    def _analyse_duplicate(self, data: bytes, raw: dict) -> str:
        """Equal TimestampMS: is it the same packet again, or new content with an
        unchanged timestamp? (Finding D-1.) Returns the classification."""
        race = raw.get("IsRaceOn")
        self.counters["dup_race_on" if race == 1 else "dup_race_off"] += 1
        prev = self._last_bytes
        if prev is None:
            self.counters["dup_changed"] += 1
            return "changed"
        kind = classify_equal(prev, bytes(data))
        self.counters[f"dup_{kind}"] += 1
        if kind == "changed":
            changed = changed_fields(prev, bytes(data))
            for k in changed:
                self.dup_changed_fields[k] += 1
            if len(self.dup_changed_samples) < 5:
                p = self._last_raw or {}
                self.dup_changed_samples.append(
                    {"t_wall": self._wall(), "is_race_on": race, "fields": changed[:12], "n_fields": len(changed),
                     "speed": [p.get("Speed"), raw.get("Speed")], "timestamp_ms": raw.get("TimestampMS")}
                )
        return kind

    def _start_segment(self, src: str, now: float) -> None:
        self.stream_segment_id = uuid.uuid4().hex[:12]
        self.segment_count += 1
        self.bound_source = src
        self.segment_started_mono = now
        self._reset_segment_state()
        self._event("segment_start", src)

    def _reject(self, now: float, reason: str, data: bytes, src: str, sample: bool = True) -> str:
        self.counters["rejected"] += 1
        self.counters["invalid_datagrams" if reason != "foreign_source" else "foreign_datagrams"] += 1
        self.reject_reasons[reason] += 1
        self.last_rejected_mono = now
        self.last_rejected_reason = reason
        if sample:
            self.rejected_samples.append(
                {"t_wall": self._wall(), "reason": reason, "length": len(data), "source": src, "head_hex": data[:64].hex()}
            )
        return f"rejected:{reason}"

    def _order(self, ts: int, now: float, data: bytes, src: str) -> str:
        last = self.last_ts
        if last is None:
            return "accept"
        if ts == last:
            return "duplicates"
        if ts > last:
            self._reset_candidates.clear()
            return "accept"
        # ts < last
        if last > U32 - WRAP_WINDOW_MS and ts < WRAP_WINDOW_MS:
            self.counters["timestamp_wraparounds"] += 1
            self._event("timestamp_wraparound", f"{last} -> {ts}")
            self._reset_candidates.clear()
            return "accept"
        recent = self.last_valid_mono is not None and now - self.last_valid_mono < 1.0
        if last - ts <= self.reorder_window_ms and recent and not self._reset_candidates:
            return "out_of_order"
        # Reset candidate: must be strictly increasing among candidates.
        if self._reset_candidates and ts <= self._reset_candidates[-1][0]:
            self._reset_candidates.clear()
        self._reset_candidates.append((ts, bytes(data), src))
        if len(self._reset_candidates) >= self.reset_confirm:
            return "reset_confirmed"
        return "reset_pending"

    def _accept(self, result: dec.DecodeResult, ts: int, now: float, src: str, data: bytes) -> None:
        raw = result.raw
        normalized, derived, quality = dec.normalize(raw)
        tail_hex = result.tail.hex()
        if raw.get("IsRaceOn") == 0:
            self.counters["accepted_race_off"] += 1
        if raw.get("CarOrdinal") == 0:
            self.counters["accepted_car_ordinal_zero"] += 1
        empty = "empty_packet" in quality["flags"]
        if empty:
            self.counters["accepted_empty"] += 1
        self.tail_values[tail_hex] += 1
        if len(self.tail_values) > 64:
            self.tail_values = Counter(dict(self.tail_values.most_common(32)))

        # gap / rate bookkeeping (estimates: UDP has no sequence number)
        if self.last_ts is not None:
            dt = (ts - self.last_ts) % U32
            if dt == 0:
                pass  # equal timestamp accepted under accept_changed: no interval information
            elif self._dt_ema is not None and dt > max(3 * self._dt_ema, 100):
                if dt > 2000:
                    self.counters["interruptions"] += 1
                else:
                    self.counters["estimated_gaps"] += 1
                    self.counters["estimated_missing_packets"] += max(0, round(dt / self._dt_ema) - 1)
            elif dt <= 200:
                self._dt_ema = dt if self._dt_ema is None else 0.9 * self._dt_ema + 0.1 * dt

        ev = self.car.update(normalized["car"]["ordinal"], now)
        if ev is not None:
            self._event(*ev)

        if self.last_valid_mono is not None and now - self.last_valid_mono > self.stale_after_s:
            self._event("resume", f"after {now - self.last_valid_mono:.1f}s")

        self.last_ts = ts
        self.last_valid_mono = now
        self.sequence += 1
        self.publish_seq += 1
        self.counters["accepted"] += 1
        self._recv_times.append(now)
        while self._recv_times and now - self._recv_times[0] > 2.0:
            self._recv_times.popleft()

        if any(q["status"] == "non_finite" for q in quality["fields"].values()):
            self.counters["frames_with_non_finite"] += 1
        quality["tail_hex"] = tail_hex
        quality["packet_length"] = result.length
        wall = self._wall()
        elapsed_ms = round((now - (self.segment_started_mono or now)) * 1000, 1)
        car_identity = self.car.as_dict(normalized["car"]["ordinal"])

        self.latest_frame = {
            "schema_version": schema.SCHEMA_VERSION,
            "api_version": API_VERSION,
            "server_instance_id": self.server_instance_id,
            "stream_segment_id": self.stream_segment_id,
            "session_id": self.stream_segment_id,  # deprecated alias
            "sequence": self.sequence,  # per segment, generated by this app, NOT by the game
            "game_timestamp_ms": ts,
            "segment_elapsed_ms": elapsed_ms,
            "received_at": round(wall * 1000),
            "mode": self.mode,
            "source": src,
            "raw": dec.json_safe_raw(raw),
            "normalized": normalized,
            "derived": derived,
            "quality": quality,
            "car_identity": car_identity,
        }
        self._latest_json = {}
        self.history.append(
            (now, ts, derived["speed_kmh"], normalized["rpm"], normalized["inputs"]["throttle"], normalized["inputs"]["brake"], normalized["inputs"]["steer"])
        )
        while self.history and now - self.history[0][0] > self.history_seconds:
            self.history.popleft()

        if self.sample_sinks:
            sample = {
                "stream_segment_id": self.stream_segment_id,
                "segment_sequence": self.sequence,
                "game_timestamp_ms": ts,
                "mono": now,
                "wall": wall,
                "mode": self.mode,
                "source": src,
                "data": bytes(data),
                "speed_kmh": derived["speed_kmh"],
                "empty": empty,
                "car_ordinal": car_identity["confirmed_ordinal"],
                "car_status": car_identity["status"],
            }
            for sink in self.sample_sinks:
                try:
                    sink(sample)
                except Exception:  # noqa: BLE001
                    self.counters["sink_errors"] += 1

    # ------------------------------------------------------------------ reads
    def _connection_state(self, now: float) -> str:
        if self.receiver_state == "error" and self.mode == "live":
            return "error"
        if self.receiver_state == "starting" and self.mode == "live":
            return "starting"
        if self.last_valid_mono is None:
            return "waiting"
        return "receiving" if now - self.last_valid_mono <= self.stale_after_s else "stale"

    def status(self) -> dict:
        now = self._clock()
        with self._lock:
            st = self._status_locked(now)
        return self._with_recorder(st)

    def _with_recorder(self, st: dict) -> dict:
        fn = self.recorder_status
        st["recorder"] = fn() if fn is not None else None
        for k, f in self.extra_status.items():
            try:
                st[k] = f()
            except Exception:  # noqa: BLE001 - optional extras never break the status
                st[k] = None
        return st

    def _status_locked(self, now: float) -> dict:
        age = None if self.last_valid_mono is None else round((now - self.last_valid_mono) * 1000)
        rejected_recent = self.last_rejected_mono is not None and now - self.last_rejected_mono < 2.0
        warning = None
        state = self._connection_state(now)
        if rejected_recent and state != "receiving":
            warning = "foreign_source" if self.last_rejected_reason == "foreign_source" else "invalid_packets"
        hz = None
        if len(self._recv_times) >= 2 and now - self._recv_times[-1] <= self.stale_after_s:
            span = self._recv_times[-1] - self._recv_times[0]
            hz = round((len(self._recv_times) - 1) / span, 1) if span > 0 else None
        return {
            "connection_state": state,
            "packet_age_ms": age,
            "traffic_warning": warning,
            "last_rejected_reason": self.last_rejected_reason if rejected_recent else None,
            "observed_hz": hz,
            "mode": self.mode,
            "receiver_state": self.receiver_state,
            "receiver_error": self.receiver_error,
            "receiver_address": self.receiver_address,
            "server_instance_id": self.server_instance_id,
            "stream_segment_id": self.stream_segment_id,
            "session_id": self.stream_segment_id,  # deprecated alias
            "source": self.bound_source,
            "sequence": self.sequence,
        }

    def message(self, include_raw: bool) -> tuple[int, str]:
        """Serialized WebSocket message: live status + latest frame (cached per publish).
        Returns (publish_seq, json) - publish_seq never resets, unlike ``sequence``."""
        now = self._clock()
        with self._lock:
            status = self._status_locked(now)
            frame_json = "null"
            if self.latest_frame is not None:
                cached = self._latest_json.get(include_raw)
                if cached and cached[0] == self.publish_seq:
                    frame_json = cached[1]
                else:
                    frame = self.latest_frame if include_raw else {k: v for k, v in self.latest_frame.items() if k != "raw"}
                    frame_json = json.dumps(frame, allow_nan=False, separators=(",", ":"))
                    self._latest_json[include_raw] = (self.publish_seq, frame_json)
            seq = self.publish_seq
        status = self._with_recorder(status)
        status_json = json.dumps(status, allow_nan=False, separators=(",", ":"))
        return seq, '{"type":"frame","status":' + status_json + ',"frame":' + frame_json + "}"

    def latest(self) -> dict:
        now = self._clock()
        with self._lock:
            status = self._status_locked(now)
            frame = None if self.latest_frame is None else dict(self.latest_frame)
        status = self._with_recorder(status)
        if frame is not None:
            frame["connection_state"] = status["connection_state"]
            frame["packet_age_ms"] = status["packet_age_ms"]
        return {"status": status, "frame": frame}

    def history_payload(self, seconds: float) -> dict:
        """History of the *current* stream segment only (reset on segment change)."""
        now = self._clock()
        with self._lock:
            rows = [r for r in self.history if now - r[0] <= seconds]
            seg = self.stream_segment_id
        cols = list(zip(*rows)) if rows else [[] for _ in range(7)]
        return {
            "server_instance_id": self.server_instance_id,
            "stream_segment_id": seg,
            "t_ago_s": [round(now - t, 4) for t in cols[0]],
            "game_timestamp_ms": list(cols[1]),
            "speed_kmh": list(cols[2]),
            "rpm": list(cols[3]),
            "throttle": list(cols[4]),
            "brake": list(cols[5]),
            "steer": list(cols[6]),
        }

    def diagnostics(self) -> dict:
        now = self._clock()
        with self._lock:
            c = self.counters
            out = {
                "status": self._status_locked(now),
                "uptime_s": round(now - self.started_at, 1),
                "counters": dict(c),
                "reject_reasons": dict(self.reject_reasons),
                "length_histogram": {str(k): v for k, v in sorted(self.length_histogram.items())},
                "tail_values_hex": dict(self.tail_values.most_common(8)),
                "other_sources": {k: {kk: vv for kk, vv in v.items() if kk != "last_seen_mono"} for k, v in self.other_sources.items()},
                "rejected_samples": list(self.rejected_samples),
                "events": list(self.events),
                "game_dt_ms_ema": None if self._dt_ema is None else round(self._dt_ema, 2),
                "segments_started": self.segment_count,
                "car_identity": self.car.as_dict(None) | {"zero_periods": self.car.zero_periods},
                "equal_timestamp_policy": self.equal_timestamp_policy,
                "duplicate_analysis": {
                    "identical": c.get("dup_identical", 0),
                    "tail_only": c.get("dup_tail_only", 0),
                    "non_finite": c.get("dup_non_finite", 0),
                    "changed": c.get("dup_changed", 0),
                    "race_on": c.get("dup_race_on", 0),
                    "race_off": c.get("dup_race_off", 0),
                    "accepted_under_policy": c.get("equal_timestamp_accepted", 0),
                    "changed_fields_top": dict(self.dup_changed_fields.most_common(10)),
                    "changed_samples": list(self.dup_changed_samples),
                },
                "bounds": {
                    "history_samples": len(self.history),
                    "history_max_samples": self.history_max_samples,
                    "other_sources": len(self.other_sources),
                    "other_sources_cap": self.other_sources_cap,
                    "events_max": self.events.maxlen,
                },
                "notes": [
                    "UDP carries no sequence number: gaps and missing packets are estimates from TimestampMS.",
                    "The game stops sending in menus, pause, replays, rewinds and after a race; silence is not proof of a network fault.",
                ],
            }
        return self._with_recorder_diag(out)

    def _with_recorder_diag(self, out: dict) -> dict:
        out["status"] = self._with_recorder(out["status"])
        return out
