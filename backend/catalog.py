"""Local car name catalog (Garage).

The game only sends a numeric ``CarOrdinal``. Pitwall ships **no** built-in
ordinal -> name list: no verified, licensed source for Forza Horizon 6 exists in this
project, and a guessed list would label cars wrongly. Names come from:

  * the user (source ``user``), typed in the Garage, and
  * JSON files the user chooses to import (source ``import:<label>``, with the file's
    version), e.g. a list shared by a friend. Imports never overwrite a user entry;
    conflicts are reported, not resolved silently.

Every entry keeps its provenance (source, source_version, created/updated time). Stock
reference data is not part of an entry; live values (power, PI, drivetrain) always come
from telemetry. Synthetic/demo ordinals (negative) and 0 ("no car") are rejected.
"""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

GAME = "fh6"
CATALOG_SCHEMA_VERSION = 1
EXPORT_FORMAT = "pitwall-car-aliases"
EXPORT_FORMAT_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS cars (
    game TEXT NOT NULL, ordinal INTEGER NOT NULL,
    name TEXT NOT NULL, year INTEGER, variant TEXT, notes TEXT,
    source TEXT NOT NULL, source_version TEXT,
    created_utc TEXT NOT NULL, updated_utc TEXT NOT NULL,
    PRIMARY KEY (game, ordinal)
);
"""


class CatalogError(ValueError):
    pass


class SchemaTooNew(RuntimeError):
    """The data was written by a newer Pitwall; this version must not modify it."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def clean_entry(e: dict[str, Any]) -> dict[str, Any]:
    """Validate one entry (user form or import line). Raises CatalogError."""
    o = e.get("ordinal")
    if isinstance(o, bool) or not isinstance(o, int):
        raise CatalogError(f"ordinal must be a whole number (got {o!r})")
    if o <= 0:
        raise CatalogError("ordinal must be positive (0 means no car; negative ordinals are synthetic demo data)")
    name = e.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CatalogError(f"car {o}: name is required")
    year = e.get("year")
    if year in ("", None):
        year = None
    elif isinstance(year, bool) or not isinstance(year, int) or not 1886 <= year <= 2100:
        raise CatalogError(f"car {o}: year must be between 1886 and 2100 (got {year!r})")

    def text(k: str, n: int) -> Optional[str]:
        v = e.get(k)
        if v is None:
            return None
        if not isinstance(v, str):
            raise CatalogError(f"car {o}: {k} must be text")
        return v.strip()[:n] or None

    return {"ordinal": o, "name": name.strip()[:120], "year": year, "variant": text("variant", 120), "notes": text("notes", 500)}


class CarCatalog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            with conn:
                conn.executescript(SCHEMA)
                row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
                if row is None:
                    conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(CATALOG_SCHEMA_VERSION),))
                elif int(row[0]) > CATALOG_SCHEMA_VERSION:
                    raise SchemaTooNew(f"car catalog schema {row[0]} is newer than this Pitwall supports ({CATALOG_SCHEMA_VERSION})")
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------ reads
    def list(self) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM cars WHERE game=? ORDER BY ordinal", (GAME,))]
        finally:
            conn.close()

    def get(self, ordinal: int) -> Optional[dict[str, Any]]:
        conn = self._conn()
        try:
            r = conn.execute("SELECT * FROM cars WHERE game=? AND ordinal=?", (GAME, ordinal)).fetchone()
            return None if r is None else dict(r)
        finally:
            conn.close()

    # ------------------------------------------------------------ writes
    def set_user(self, entry: dict[str, Any]) -> dict[str, Any]:
        e = clean_entry(entry)
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    old = conn.execute("SELECT created_utc FROM cars WHERE game=? AND ordinal=?", (GAME, e["ordinal"])).fetchone()
                    conn.execute(
                        "INSERT OR REPLACE INTO cars VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (GAME, e["ordinal"], e["name"], e["year"], e["variant"], e["notes"], "user", None, old[0] if old else now, now),
                    )
            finally:
                conn.close()
        return self.get(e["ordinal"])  # type: ignore[return-value]

    def delete(self, ordinal: int) -> bool:
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    return conn.execute("DELETE FROM cars WHERE game=? AND ordinal=?", (GAME, ordinal)).rowcount > 0
            finally:
                conn.close()

    def import_file(self, data: Any) -> dict[str, Any]:
        """Import a JSON document in the export format. User entries are never replaced;
        an entry from the same import source is updated; other conflicts are listed."""
        if not isinstance(data, dict) or data.get("format") != EXPORT_FORMAT:
            raise CatalogError(f"not a Pitwall car list (expected format '{EXPORT_FORMAT}')")
        if data.get("game") != GAME:
            raise CatalogError(f"this list is for game {data.get('game')!r}, not {GAME!r}: ordinals are not shared between games")
        label = data.get("source")
        if not isinstance(label, str) or not label.strip():
            raise CatalogError("the list must name its source (field 'source')")
        source = f"import:{label.strip()[:80]}"
        version = str(data.get("version") or "")[:40] or None
        cars = data.get("cars")
        if not isinstance(cars, list) or len(cars) > 5000:
            raise CatalogError("'cars' must be a list of at most 5000 entries")
        added = updated = 0
        kept_user: list[int] = []
        conflicts: list[dict] = []
        invalid: list[str] = []
        now = _now()
        with self._lock:
            conn = self._conn()
            try:
                with conn:
                    for raw in cars:
                        try:
                            e = clean_entry(raw if isinstance(raw, dict) else {})
                        except CatalogError as err:
                            invalid.append(str(err))
                            continue
                        old = conn.execute("SELECT source, name, created_utc FROM cars WHERE game=? AND ordinal=?", (GAME, e["ordinal"])).fetchone()
                        if old is not None and old["source"] == "user":
                            kept_user.append(e["ordinal"])
                            continue
                        if old is not None and old["source"] != source:
                            conflicts.append({"ordinal": e["ordinal"], "kept": old["name"], "kept_source": old["source"], "offered": e["name"]})
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO cars VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (GAME, e["ordinal"], e["name"], e["year"], e["variant"], e["notes"], source, version, old["created_utc"] if old else now, now),
                        )
                        if old is None:
                            added += 1
                        else:
                            updated += 1
            finally:
                conn.close()
        return {"source": source, "version": version, "added": added, "updated": updated, "kept_user_entries": kept_user[:200],
                "conflicts": conflicts[:200], "invalid": invalid[:50]}

    def export(self, source_label: str = "my Pitwall garage") -> dict[str, Any]:
        cars = [{k: r[k] for k in ("ordinal", "name", "year", "variant", "notes")} | {"provenance": r["source"]} for r in self.list()]
        return {"format": EXPORT_FORMAT, "format_version": EXPORT_FORMAT_VERSION, "game": GAME, "source": source_label,
                "version": _now()[:10], "exported_utc": _now(), "cars": cars}


def seen_cars(recordings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cars with a confirmed identity in saved recordings (from each recording's summary)."""
    seen: dict[int, dict[str, Any]] = {}
    for rec in recordings:
        cars = (rec.get("summary") or {}).get("cars_confirmed") or {}
        for k, n in cars.items():
            try:
                o = int(float(k))
            except ValueError:
                continue
            if o <= 0:
                continue  # synthetic/demo ordinals are not catalogued
            s = seen.setdefault(o, {"ordinal": o, "recordings": 0, "samples": 0, "last_seen_utc": None, "car_labels": []})
            s["recordings"] += 1
            s["samples"] += int(n)
            if s["last_seen_utc"] is None or rec["started_utc"] > s["last_seen_utc"]:
                s["last_seen_utc"] = rec["started_utc"]
            if rec.get("car_label") and rec["car_label"] not in s["car_labels"]:
                s["car_labels"].append(rec["car_label"])
    return sorted(seen.values(), key=lambda s: s["last_seen_utc"] or "", reverse=True)


def dumps_export(doc: dict[str, Any]) -> str:
    return json.dumps(doc, indent=1, ensure_ascii=False)
