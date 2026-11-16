"""Bounded diagnostic capture (D-1 evidence collection).

A user-started, time- and size-limited capture of *every* datagram the receiver hands to
the hub (accepted and rejected), with arrival order, process-relative monotonic time,
source, outcome and the raw bytes. It taps the existing ingest chain (no second socket
bind on the game port) and only appends to memory while capturing; the gzip file is
written by a separate thread after the capture ends.

Captures are temporary diagnostics with their own quota (oldest capture files beyond
``keep_files`` are removed); user recordings are never touched by this module.
"""

from __future__ import annotations

import datetime as _dt
import gzip
import json
import math
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import decode as dec
from . import schema

MIN_SECONDS, MAX_SECONDS = 5, 120


def _utc(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def classify_equal(prev: bytes, cur: bytes) -> str:
    """Compare two datagrams that carry the same TimestampMS.

    identical   byte-for-byte the same
    tail_only   documented 323 bytes equal, only the opaque trailing byte differs
    non_finite  differences involve NaN/Inf fields only
    changed     at least one finite documented field differs
    """
    if prev == cur:
        return "identical"
    end = schema.FIELDS_END
    if prev[:end] == cur[:end]:
        return "tail_only"
    a, b = dec.decode(prev), dec.decode(cur)
    if not (a.ok and b.ok):
        return "changed"
    finite_diff = False
    for k, v in b.raw.items():
        w = a.raw.get(k)
        if v == w:
            continue
        both_nan = isinstance(v, float) and isinstance(w, float) and math.isnan(v) and math.isnan(w)
        if both_nan:
            continue
        nonfinite = any(isinstance(x, float) and not math.isfinite(x) for x in (v, w))
        if not nonfinite:
            finite_diff = True
            break
    return "changed" if finite_diff else "non_finite"


def changed_fields(prev: bytes, cur: bytes) -> list[str]:
    a, b = dec.decode(prev), dec.decode(cur)
    if not (a.ok and b.ok):
        return []
    out = []
    for k, v in b.raw.items():
        w = a.raw.get(k)
        if v != w and not (isinstance(v, float) and isinstance(w, float) and math.isnan(v) and math.isnan(w)):
            out.append(k)
    return out


def analyse_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Offline analysis of capture records (dicts with outcome + hex)."""
    outcomes: dict[str, int] = {}
    equal = {"identical": 0, "tail_only": 0, "non_finite": 0, "changed": 0}
    fields_hist: dict[str, int] = {}
    race_off = 0
    last_accepted: Optional[bytes] = None
    gaps_ms: list[float] = []
    prev_t = None
    n = 0
    for r in records:
        n += 1
        outcomes[r["outcome"]] = outcomes.get(r["outcome"], 0) + 1
        data = bytes.fromhex(r["hex"])
        if prev_t is not None:
            gaps_ms.append(r["t_ms"] - prev_t)
        prev_t = r["t_ms"]
        if r["outcome"] == "duplicates" and last_accepted is not None:
            kind = classify_equal(last_accepted, data)
            equal[kind] += 1
            if kind == "changed":
                for f in changed_fields(last_accepted, data):
                    fields_hist[f] = fields_hist.get(f, 0) + 1
            res = dec.decode(data)
            if res.ok and res.raw.get("IsRaceOn") == 0:
                race_off += 1
        if r["outcome"] == "accepted":
            last_accepted = data
    gaps_ms.sort()
    return {
        "datagrams": n,
        "outcomes": outcomes,
        "equal_timestamp": equal,
        "equal_timestamp_race_off": race_off,
        "changed_fields_top": dict(sorted(fields_hist.items(), key=lambda kv: -kv[1])[:12]),
        "arrival_gap_ms": None if not gaps_ms else {
            "p50": round(gaps_ms[len(gaps_ms) // 2], 3),
            "p95": round(gaps_ms[min(len(gaps_ms) - 1, int(0.95 * len(gaps_ms)))], 3),
            "max": round(gaps_ms[-1], 3),
        },
    }


def read_capture(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    header: dict[str, Any] = {}
    records: list[dict[str, Any]] = []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("type") == "header":
                header = obj
            else:
                records.append(obj)
    return header, records


class DiagnosticCapture:
    def __init__(self, directory: Path, *, max_bytes: int = 16 << 20, keep_files: int = 10, app_version: str = "?",
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] = time.time) -> None:
        self.dir = directory
        self.max_bytes = max_bytes
        self.keep_files = keep_files
        self.app_version = app_version
        self._clock, self._wall = clock, wall
        self._lock = threading.Lock()
        self.state = "idle"  # idle | capturing | writing | error
        self.error: Optional[str] = None
        self._rows: list[tuple] = []
        self._bytes = 0
        self._t0 = 0.0
        self._end = 0.0
        self._id: Optional[str] = None
        self._mask = False
        self._started_wall = 0.0
        self._stop_reason = ""
        self.last_result: Optional[dict[str, Any]] = None
        self._writer: Optional[threading.Thread] = None

    # ----------------------------------------------------------- control
    def start(self, seconds: float, mask_sources: bool = False) -> dict[str, Any]:
        if not (MIN_SECONDS <= seconds <= MAX_SECONDS):
            raise ValueError(f"seconds must be within {MIN_SECONDS}..{MAX_SECONDS}")
        with self._lock:
            if self.state in ("capturing", "writing"):
                raise RuntimeError(self.state)
            self.state = "capturing"
            self.error = None
            self._rows = []
            self._bytes = 0
            self._t0 = self._clock()
            self._end = self._t0 + seconds
            self._id = uuid.uuid4().hex[:8]
            self._mask = bool(mask_sources)
            self._started_wall = self._wall()
            self._stop_reason = ""
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "capturing":
                self._finish_locked("stopped by user")
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            if self.state == "capturing" and self._clock() >= self._end:
                self._finish_locked("duration reached")
            return {
                "state": self.state,
                "id": self._id,
                "datagrams": len(self._rows) if self.state == "capturing" else None,
                "bytes": self._bytes if self.state == "capturing" else None,
                "remaining_s": round(max(0.0, self._end - self._clock()), 1) if self.state == "capturing" else None,
                "max_bytes": self.max_bytes,
                "error": self.error,
                "last_result": self.last_result,
            }

    # ----------------------------------------------------------- tap (hot path)
    def tap(self, data: bytes, src: str, outcome: str) -> None:
        if self.state != "capturing":  # cheap unlocked check; exact check below
            return
        now = self._clock()
        with self._lock:
            if self.state != "capturing":
                return
            if now >= self._end:
                self._finish_locked("duration reached")
                return
            self._rows.append((len(self._rows), now - self._t0, src, outcome, bytes(data)))
            self._bytes += len(data) + 64
            if self._bytes >= self.max_bytes:
                self._finish_locked("size limit reached")

    # ----------------------------------------------------------- writing
    def _finish_locked(self, reason: str) -> None:
        self.state = "writing"
        self._stop_reason = reason
        rows, self._rows = self._rows, []
        meta = {"id": self._id, "started_wall": self._started_wall, "duration_s": round(self._clock() - self._t0, 3), "mask": self._mask, "reason": reason}
        self._writer = threading.Thread(target=self._write, args=(rows, meta), name="capture-writer", daemon=True)
        self._writer.start()

    def wait(self, timeout: float = 10.0) -> None:
        w = self._writer
        if w is not None:
            w.join(timeout)

    def _write(self, rows: list[tuple], meta: dict[str, Any]) -> None:
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            name = f"capture-{_utc(meta['started_wall'])}-{meta['id']}"
            path = self.dir / f"{name}.jsonl.gz"
            tmp = path.with_suffix(".gz.partial")
            masks: dict[str, str] = {}
            records = []
            for i, t, src, outcome, data in rows:
                if meta["mask"]:
                    src = masks.setdefault(src, f"source-{len(masks) + 1}")
                records.append({"i": i, "t_ms": round(t * 1000, 3), "src": src, "outcome": outcome, "len": len(data), "hex": data.hex()})
            header = {
                "type": "header",
                "format": "pitwall-diagnostic-capture",
                "format_version": 1,
                "app_version": self.app_version,
                "schema_version": schema.SCHEMA_VERSION,
                "started_utc": _dt.datetime.fromtimestamp(meta["started_wall"], _dt.timezone.utc).isoformat(timespec="seconds"),
                "duration_s": meta["duration_s"],
                "stop_reason": meta["reason"],
                "sources_masked": meta["mask"],
                "time_base": "t_ms = process monotonic time since capture start (not game time, not wall clock)",
            }
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                f.write(json.dumps(header) + "\n")
                for r in records:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            tmp.replace(path)
            summary = analyse_records(records)
            summary.update({"file": path.name, "started_utc": header["started_utc"], "duration_s": meta["duration_s"], "stop_reason": meta["reason"], "bytes_on_disk": path.stat().st_size})
            (self.dir / f"{name}.summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
            self._enforce_quota()
            with self._lock:
                self.last_result = summary
                self.state = "idle"
        except Exception as e:  # noqa: BLE001 - report, never crash the app
            with self._lock:
                self.state = "error"
                self.error = f"capture could not be written: {e}"

    def _enforce_quota(self) -> None:
        files = sorted(self.dir.glob("capture-*.jsonl.gz"))
        for old in files[: max(0, len(files) - self.keep_files)]:
            old.unlink(missing_ok=True)
            Path(str(old).replace(".jsonl.gz", ".summary.json")).unlink(missing_ok=True)

    def list(self) -> list[dict[str, Any]]:
        out = []
        for s in sorted(self.dir.glob("capture-*.summary.json"), reverse=True):
            try:
                out.append(json.loads(s.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
        return out
