"""Car catalog, import provenance and format compatibility tests."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.catalog import CarCatalog, CatalogError, SchemaTooNew, clean_entry, seen_cars
from backend.config import Config
from backend.recording.recorder import Recorder
from backend.recording.store import SchemaTooNew as StoreTooNew
from backend.recording.store import Store
from backend.telemetry.hub import TelemetryHub


@pytest.fixture
def cat(tmp_path):
    return CarCatalog(tmp_path / "catalog.sqlite3")


def doc(source="friend list", version="2026-11", cars=None, game="fh6"):
    return {"format": "pitwall-car-aliases", "format_version": 1, "game": game, "source": source, "version": version, "cars": cars or []}


@pytest.mark.parametrize(
    "entry, msg",
    [
        ({"ordinal": -2, "name": "x"}, "synthetic"),
        ({"ordinal": 0, "name": "x"}, "no car"),
        ({"ordinal": True, "name": "x"}, "whole number"),
        ({"ordinal": "4210", "name": "x"}, "whole number"),
        ({"ordinal": 4210, "name": "  "}, "name is required"),
        ({"ordinal": 4210, "name": "x", "year": 1500}, "year"),
        ({"ordinal": 4210, "name": "x", "variant": 5}, "variant must be text"),
    ],
)
def test_entry_validation(entry, msg):
    with pytest.raises(CatalogError, match=msg):
        clean_entry(entry)


def test_user_entry_keeps_created_time_and_provenance(cat):
    a = cat.set_user({"ordinal": 4210, "name": "My hypercar", "year": 2024})
    b = cat.set_user({"ordinal": 4210, "name": "My hypercar (tuned)", "year": 2024, "variant": "AWD swap"})
    assert a["created_utc"] == b["created_utc"] and b["source"] == "user" and b["variant"] == "AWD swap"
    assert cat.delete(4210) and not cat.delete(4210)


def test_import_never_overwrites_user_names_and_reports_conflicts(cat):
    cat.set_user({"ordinal": 1, "name": "Mine"})
    r = cat.import_file(doc(cars=[{"ordinal": 1, "name": "Theirs"}, {"ordinal": 2, "name": "Two"}, {"ordinal": -5, "name": "demo"}, "junk"]))
    assert r["added"] == 1 and r["kept_user_entries"] == [1] and len(r["invalid"]) == 2
    assert cat.get(1)["name"] == "Mine" and cat.get(2)["source"] == "import:friend list" and cat.get(2)["source_version"] == "2026-11"
    # same source again updates its own entries
    r2 = cat.import_file(doc(version="2026-12", cars=[{"ordinal": 2, "name": "Two (fixed)"}]))
    assert r2["updated"] == 1 and cat.get(2)["name"] == "Two (fixed)"
    # a different source does not silently replace it
    r3 = cat.import_file(doc(source="other list", cars=[{"ordinal": 2, "name": "Deux"}]))
    assert r3["conflicts"] == [{"ordinal": 2, "kept": "Two (fixed)", "kept_source": "import:friend list", "offered": "Deux"}]


@pytest.mark.parametrize(
    "bad, msg",
    [({"format": "csv"}, "not a Pitwall car list"), (doc(game="fm"), "not 'fh6'"), (doc(source=""), "name its source")],
)
def test_import_rejects_foreign_files(cat, bad, msg):
    with pytest.raises(CatalogError, match=msg):
        cat.import_file(bad)


def test_export_import_roundtrip(tmp_path, cat):
    cat.set_user({"ordinal": 7, "name": "Seven", "year": 1999, "notes": "rally"})
    exported = cat.export()
    other = CarCatalog(tmp_path / "other.sqlite3")
    r = other.import_file(json.loads(json.dumps(exported)))
    assert r["added"] == 1 and other.get(7)["notes"] == "rally" and other.get(7)["source"].startswith("import:")


def test_newer_catalog_schema_is_not_touched(tmp_path):
    p = tmp_path / "c.sqlite3"
    CarCatalog(p)
    with sqlite3.connect(p) as c:
        c.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(SchemaTooNew):
        CarCatalog(p)


def test_newer_recordings_schema_disables_recording_without_changes(tmp_path):
    root = tmp_path / "recordings"
    Store(root).init()
    with sqlite3.connect(root / "pitwall.sqlite3") as c:
        c.execute("UPDATE meta SET value='2' WHERE key='db_schema_version'")
        before = c.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()
    with pytest.raises(StoreTooNew, match="newer Pitwall"):
        Recorder(Store(root), app_version="old")
    with sqlite3.connect(root / "pitwall.sqlite3") as c:
        assert c.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == before


def test_seen_cars_from_recording_summaries():
    recs = [
        {"started_utc": "2026-11-16T10:00:00", "car_label": "red one", "summary": {"cars_confirmed": {"4210": 100, "-2": 50}}},
        {"started_utc": "2026-11-17T10:00:00", "car_label": None, "summary": {"cars_confirmed": {"4210.0": 10, "2010": 5}}},
        {"started_utc": "2026-11-15T10:00:00", "car_label": None, "summary": None},
    ]
    s = seen_cars(recs)
    assert [x["ordinal"] for x in s] == [4210, 2010]  # most recent first; demo ordinal excluded
    assert s[0]["recordings"] == 2 and s[0]["samples"] == 110 and s[0]["car_labels"] == ["red one"]


def test_cars_api(tmp_path):
    cfg = Config(udp_port=0, extra_allowed_hosts=["testserver"])
    app = create_app(cfg, TelemetryHub(), recorder=Recorder(Store(tmp_path / "rec"), app_version="t"),
                     catalog=CarCatalog(tmp_path / "cat.sqlite3"), paths=cfg.paths())
    with TestClient(app) as c:
        assert c.get("/api/v1/capabilities").json()["features"]["car_catalog"] is True
        assert c.put("/api/v1/cars/4210", json={"name": "Hyper", "year": 2024}).json()["source"] == "user"
        assert c.put("/api/v1/cars/-2", json={"name": "demo"}).status_code == 422
        assert c.put("/api/v1/cars/4210", json={"name": "x"}, headers={"origin": "http://evil.example"}).status_code == 403
        body = c.get("/api/v1/cars").json()
        assert body["catalog"][0]["name"] == "Hyper" and "no built-in list" in body["note"]
        exp = c.get("/api/v1/cars/export")
        assert "attachment" in exp.headers["content-disposition"] and exp.json()["cars"][0]["ordinal"] == 4210
        r = c.post("/api/v1/cars/import", content=json.dumps(doc(cars=[{"ordinal": 4210, "name": "Other"}, {"ordinal": 9, "name": "Nine"}])))
        assert r.json()["kept_user_entries"] == [4210] and r.json()["added"] == 1
        assert c.post("/api/v1/cars/import", content=b"{not json").status_code == 422
        assert c.delete("/api/v1/cars/9").status_code == 200 and c.delete("/api/v1/cars/9").status_code == 404
        assert c.get("/api/v1/paths").json()["car_catalog"].endswith("catalog.sqlite3")
