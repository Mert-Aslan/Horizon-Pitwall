"""Recording storage: SQLite metadata + independent gzip JSONL chunk files.

Layout (under ``<data_dir>/recordings``)::

    pitwall.sqlite3                  metadata (WAL)
    <recording_id>/chunk-00000.jsonl.gz
    <recording_id>/chunk-00001.jsonl.gz.partial   (being written; never referenced by the DB)

Ownership: the recorder's single writer thread is the only code that writes to the
database (``Store.writer()``); HTTP handlers use short-lived read connections
(``Store.read()``). Transactions are explicit (``with conn:`` commits or rolls back);
this does not rely on Python 3.12+'s ``autocommit`` attribute, so behaviour is the same
on 3.11 and 3.13.

Chunk line format (format "pitwall-jsonl-v1"), one JSON object per accepted datagram::

    {"i": recording sample index, "seg": segment index, "sq": sequence within the stream
     segment, "ts": game TimestampMS, "el": seconds since recording start (process
     monotonic), "w": UTC wall clock ms, "d": base64 of the original datagram}

Only the original datagram is stored (lossless); normalized values are re-derived with
the decoder on read. A future decoder can re-read old recordings; files are never
rewritten in place.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import sqlite3
import zlib
from pathlib import Path
from typing import Any, Iterator, Optional

CHUNK_FORMAT = "pitwall-jsonl-v1"
DB_SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS recordings (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,              -- recording | completed | failed | recovered | incomplete
    mode TEXT NOT NULL,                -- live | demo
    title TEXT, notes TEXT, car_label TEXT, setup_label TEXT,
    started_utc TEXT NOT NULL, ended_utc TEXT,
    duration_s REAL, samples INTEGER DEFAULT 0, dropped INTEGER DEFAULT 0,
    lost_on_stop INTEGER DEFAULT 0, segments INTEGER DEFAULT 0,
    app_version TEXT, schema_version INTEGER, decoder TEXT, chunk_format TEXT,
    stop_reason TEXT, error TEXT, summary_json TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    recording_id TEXT NOT NULL, segment_index INTEGER NOT NULL,
    stream_segment_id TEXT, source TEXT, first_sample INTEGER, start_elapsed_s REAL,
    PRIMARY KEY (recording_id, segment_index)
);
CREATE TABLE IF NOT EXISTS chunks (
    recording_id TEXT NOT NULL, chunk_index INTEGER NOT NULL, file TEXT NOT NULL,
    first_sample INTEGER, last_sample INTEGER, samples INTEGER,
    t_start_s REAL, t_end_s REAL, bytes INTEGER, sha256 TEXT,
    status TEXT NOT NULL,              -- ok | recovered | missing
    PRIMARY KEY (recording_id, chunk_index)
);
CREATE TABLE IF NOT EXISTS events (
    recording_id TEXT NOT NULL, sample_index INTEGER, elapsed_s REAL,
    type TEXT, detail TEXT, stream_segment_id TEXT
);
CREATE INDEX IF NOT EXISTS events_by_rec ON events (recording_id, sample_index);
CREATE TABLE IF NOT EXISTS annotations (   -- reserved for markers/notes (M3/M4)
    id INTEGER PRIMARY KEY AUTOINCREMENT, recording_id TEXT NOT NULL,
    elapsed_s REAL, text TEXT, created_utc TEXT
);
"""

EDITABLE = ("title", "notes", "car_label", "setup_label")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 16), b""):
            h.update(block)
    return h.hexdigest()


def encode_line(i: int, seg: int, sq: int, ts: int, el: float, wall_ms: int, data: bytes) -> bytes:
    return (
        '{"i":%d,"seg":%d,"sq":%d,"ts":%d,"el":%.4f,"w":%d,"d":"%s"}\n'
        % (i, seg, sq, ts, el, wall_ms, base64.b64encode(data).decode("ascii"))
    ).encode("ascii")


def read_chunk(path: Path) -> list[dict[str, Any]]:
    with gzip.open(path, "rt", encoding="ascii") as f:
        return [json.loads(line) for line in f]


def recover_partial(path: Path) -> list[bytes]:
    """Complete lines from a possibly truncated gzip stream (no trailer, cut mid-line)."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    buf = b""
    with open(path, "rb") as f:
        while True:
            block = f.read(1 << 16)
            if not block:
                break
            try:
                buf += d.decompress(block)
            except zlib.error:
                break
    lines = []
    for line in buf.split(b"\n")[:-1]:  # the last piece is incomplete (or empty)
        try:
            json.loads(line)
        except ValueError:
            break
        lines.append(line + b"\n")
    return lines


def decode_datagram(line: dict[str, Any]) -> bytes:
    return base64.b64decode(line["d"])


class Store:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.db_path = root / "pitwall.sqlite3"

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        conn = self.writer()
        try:
            with conn:
                conn.executescript(SCHEMA)
                conn.execute("INSERT OR IGNORE INTO meta VALUES ('db_schema_version', ?)", (str(DB_SCHEMA_VERSION),))
        finally:
            conn.close()

    def writer(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def read(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def rec_dir(self, rec_id: str) -> Path:
        return self.root / rec_id

    # ------------------------------------------------------------------ reads
    def list(self, limit: int = 200) -> list[dict[str, Any]]:
        if not self.db_path.exists():
            return []
        conn = self.read()
        try:
            rows = conn.execute("SELECT * FROM recordings ORDER BY started_utc DESC LIMIT ?", (limit,)).fetchall()
            return [_rec_row(r) for r in rows]
        finally:
            conn.close()

    def get(self, rec_id: str) -> Optional[dict[str, Any]]:
        if not self.db_path.exists():
            return None
        conn = self.read()
        try:
            r = conn.execute("SELECT * FROM recordings WHERE id = ?", (rec_id,)).fetchone()
            if r is None:
                return None
            out = _rec_row(r)
            out["segments_detail"] = [dict(x) for x in conn.execute("SELECT * FROM segments WHERE recording_id = ? ORDER BY segment_index", (rec_id,))]
            out["chunks"] = [dict(x) for x in conn.execute("SELECT * FROM chunks WHERE recording_id = ? ORDER BY chunk_index", (rec_id,))]
            out["events"] = [dict(x) for x in conn.execute("SELECT sample_index, elapsed_s, type, detail, stream_segment_id FROM events WHERE recording_id = ? ORDER BY rowid LIMIT 500", (rec_id,))]
            return out
        finally:
            conn.close()

    def update_metadata(self, rec_id: str, changes: dict[str, Any]) -> bool:
        """User-entered labels/notes. A short write transaction from the API thread; the
        recorder's writer thread owns all sample/chunk writes. SQLite serialises the two
        (WAL + busy timeout)."""
        if not self.db_path.exists():
            return False
        changes = {k: v for k, v in changes.items() if k in EDITABLE}
        conn = self.writer()
        try:
            with conn:
                if conn.execute("SELECT 1 FROM recordings WHERE id=?", (rec_id,)).fetchone() is None:
                    return False
                for k, v in changes.items():
                    conn.execute(f"UPDATE recordings SET {k}=? WHERE id=?", (v or None, rec_id))  # k is from EDITABLE
            return True
        finally:
            conn.close()

    def iter_lines(self, rec_id: str, t_from: float = 0.0, t_to: float = float("inf")) -> Iterator[dict[str, Any]]:
        """Lines whose recording elapsed time is within [t_from, t_to]; only chunks whose
        time range overlaps are opened (chunk index = coarse time index)."""
        rec = self.get(rec_id)
        if rec is None:
            return
        for c in rec["chunks"]:
            if c["status"] == "missing" or c["t_end_s"] < t_from or c["t_start_s"] > t_to:
                continue
            for line in read_chunk(self.rec_dir(rec_id) / c["file"]):
                if t_from <= line["el"] <= t_to:
                    yield line

    def verify(self, rec_id: str) -> dict[str, Any]:
        rec = self.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        problems: list[str] = []
        total = 0
        expected_next = 0
        for c in rec["chunks"]:
            p = self.rec_dir(rec_id) / c["file"]
            if c["status"] == "missing" or not p.exists():
                problems.append(f"chunk {c['chunk_index']}: file missing")
                continue
            if sha256_file(p) != c["sha256"]:
                problems.append(f"chunk {c['chunk_index']}: checksum mismatch")
                continue
            lines = read_chunk(p)
            if len(lines) != c["samples"]:
                problems.append(f"chunk {c['chunk_index']}: {len(lines)} lines, manifest says {c['samples']}")
            if lines and lines[0]["i"] != expected_next:
                problems.append(f"chunk {c['chunk_index']}: starts at sample {lines[0]['i']}, expected {expected_next}")
            if lines:
                expected_next = lines[-1]["i"] + 1
            total += len(lines)
        if total != rec["samples"]:
            problems.append(f"{total} samples in files, recording says {rec['samples']}")
        return {"recording_id": rec_id, "ok": not problems, "samples_in_files": total, "chunks": len(rec["chunks"]), "problems": problems}


def _rec_row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    d["summary"] = json.loads(d.pop("summary_json") or "null")
    return d
