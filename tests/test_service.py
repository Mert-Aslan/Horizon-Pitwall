"""Service-mode logic that runs without the Service Control Manager or elevation:
configuration, safe file operations, user-data copy and the service body."""

import json
import socket
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows service")

from backend import runtime  # noqa: E402
from backend.service import manage, svc  # noqa: E402


def free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def test_locations_follow_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PROGRAMDATA", str(tmp_path / "pd"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    assert manage.program_data_root() == tmp_path / "pd" / "HorizonPitwall"
    assert manage.install_root() == tmp_path / "pf" / "Horizon Pitwall"


def test_service_config_forces_service_data_dir(tmp_path):
    (tmp_path / "pitwall.config.json").write_text(json.dumps({"web_port": 18123, "data_dir": "C:\\Users\\x"}), encoding="utf-8")
    cfg = manage.load_service_config(tmp_path)
    assert cfg.web_port == 18123
    assert cfg.data_path == tmp_path / "data"
    assert manage.service_url(tmp_path) == "http://127.0.0.1:18123/"


def test_safe_rmtree_refuses_outside_and_unmarked(tmp_path):
    base = tmp_path / "base"
    (base / "inst").mkdir(parents=True)
    with pytest.raises(manage.ServiceError):
        manage.safe_rmtree(base, must_be_in=base)
    with pytest.raises(manage.ServiceError):
        manage.safe_rmtree(tmp_path / "elsewhere", must_be_in=base)
    with pytest.raises(manage.ServiceError):
        manage.safe_rmtree(base / ".." / "base", must_be_in=base)
    with pytest.raises(manage.ServiceError):
        manage.safe_rmtree(base / "inst", must_be_in=base, marker=manage.INSTALL_MARKER)
    (base / "inst" / manage.INSTALL_MARKER).write_text("x")
    manage.safe_rmtree(base / "inst", must_be_in=base, marker=manage.INSTALL_MARKER)
    assert not (base / "inst").exists()


def test_copy_release_requires_release_and_replaces_only_own_install(tmp_path):
    src = tmp_path / "rel"
    src.mkdir()
    with pytest.raises(manage.ServiceError):
        manage.copy_release(src, tmp_path / "pf" / "Horizon Pitwall")
    (src / manage.APP_EXE).write_text("exe")
    (src / "_internal").mkdir()
    (src / "_internal" / "lib.dll").write_text("dll")
    target = tmp_path / "pf" / "Horizon Pitwall"
    target.mkdir(parents=True)
    (target / "foreign.txt").write_text("not ours")
    with pytest.raises(manage.ServiceError):
        manage.copy_release(src, target)
    assert (target / "foreign.txt").exists()
    (target / "foreign.txt").unlink()
    target.rmdir()
    manage.copy_release(src, target)
    assert (target / manage.APP_EXE).exists() and (target / manage.INSTALL_MARKER).exists()
    (src / "_internal" / "lib.dll").write_text("v2")
    manage.copy_release(src, target)  # upgrade in place
    assert (target / "_internal" / "lib.dll").read_text() == "v2"
    assert not (target.parent / "Horizon Pitwall.new").exists()
    assert not (target.parent / "Horizon Pitwall.old").exists()


def _db(p: Path, rows: int) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p)
    c.execute("create table t(x)")
    c.executemany("insert into t values (?)", [(i,) for i in range(rows)])
    c.commit()
    c.close()


def test_copy_user_data_copies_and_never_merges(tmp_path):
    user, svcdata = tmp_path / "user", tmp_path / "svc"
    _db(user / "recordings" / "pitwall.sqlite3", 5)
    (user / "recordings" / "rec1").mkdir()
    (user / "recordings" / "rec1" / "c0.jsonl.gz").write_bytes(b"x")
    _db(user / "catalog.sqlite3", 2)
    notes = manage.copy_user_data(user, svcdata)
    assert len(notes) == 2
    assert (svcdata / "recordings" / "rec1" / "c0.jsonl.gz").exists()
    c = sqlite3.connect(svcdata / "recordings" / "pitwall.sqlite3")
    assert c.execute("select count(*) from t").fetchone()[0] == 5
    c.close()
    assert (user / "recordings" / "pitwall.sqlite3").exists()  # originals kept
    notes = manage.copy_user_data(user, svcdata)  # second time: refuses to merge
    assert all("not copied" in n for n in notes)


def test_service_body_serves_and_stops(tmp_path):
    web, udp = free_port(), free_port(socket.SOCK_DGRAM)
    (tmp_path / "pitwall.config.json").write_text(json.dumps({"web_port": web, "udp_port": udp}), encoding="utf-8")
    stop, running, result = threading.Event(), threading.Event(), {}
    t = threading.Thread(target=lambda: result.setdefault("code", svc.run_body(stop, running.set, tmp_path)))
    t.start()
    try:
        assert running.wait(30)
        assert runtime.probe(f"http://127.0.0.1:{web}/") is not None
        assert (tmp_path / "data" / "logs" / "pitwall.log").exists()
    finally:
        stop.set()
        t.join(40)
    assert result["code"] == 0
    assert runtime.port_free("127.0.0.1", web) is None


def test_service_body_port_busy(tmp_path):
    web, udp = free_port(), free_port(socket.SOCK_DGRAM)
    (tmp_path / "pitwall.config.json").write_text(json.dumps({"web_port": web, "udp_port": udp}), encoding="utf-8")
    s = socket.socket()
    s.bind(("127.0.0.1", web))
    s.listen()
    try:
        assert svc.run_body(threading.Event(), lambda: None, tmp_path) == 3
    finally:
        s.close()


def test_status_when_not_installed(monkeypatch):
    monkeypatch.setattr(manage, "state", lambda: None)
    assert "not installed" in manage.cmd_status()


def test_uninstall_refuses_to_run_from_installed_copy(monkeypatch, tmp_path):
    target = tmp_path / "Horizon Pitwall"
    target.mkdir()
    (target / manage.INSTALL_MARKER).write_text("x")
    monkeypatch.setattr(manage, "install_root", lambda: target)
    monkeypatch.setattr(manage.paths, "install_dir", lambda: target)
    called = []
    monkeypatch.setattr(manage, "state", lambda: called.append(1) or "running")
    with pytest.raises(manage.ServiceError, match="release folder"):
        manage.cmd_uninstall(False)
    assert not called and target.exists()  # nothing was changed


def test_secure_program_data_children_inherit_service_access(tmp_path):
    import subprocess

    root = tmp_path / "pd"
    (root / "data" / "recordings").mkdir(parents=True)
    (root / "pitwall.config.json").write_text("{}", encoding="utf-8")
    (root / "data" / "recordings" / "pitwall.sqlite3").write_bytes(b"x")
    try:
        me = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True).stdout.strip().split(",")[-1].strip('"')
        manage.secure_program_data(root, extra_grants=(f"*{me}:(OI)(CI)F",))  # the test runs unelevated
        for f in (root / "pitwall.config.json", root / "data" / "recordings" / "pitwall.sqlite3"):
            aces = _aces(f)
            assert ("S-1-5-19", True) in {(sid, inherited) for sid, inherited, _ in aces}, aces  # LOCAL SERVICE, inherited
            assert all(sid != "S-1-5-32-545" for sid, _, _ in aces), aces  # no BUILTIN\Users
    finally:
        # Give the folder back to normal inheritance so pytest can clean it up.
        subprocess.run(["icacls", str(root), "/reset", "/T", "/C", "/Q"], capture_output=True)


def _aces(path):
    """(SID, inherited, access mask) for every ACE of a file's DACL."""
    import win32security

    sd = win32security.GetFileSecurity(str(path), win32security.DACL_SECURITY_INFORMATION)
    dacl = sd.GetSecurityDescriptorDacl()
    out = []
    for i in range(dacl.GetAceCount()):
        (_type, flags), mask, sid = dacl.GetAce(i)
        out.append((win32security.ConvertSidToStringSid(sid), bool(flags & 0x10), mask))  # 0x10 = INHERITED_ACE
    return out
