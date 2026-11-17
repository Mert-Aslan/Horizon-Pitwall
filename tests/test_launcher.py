"""Launcher lifecycle: singleton, instance verification, exit IPC, autostart and the
startup error paths. Uses isolated ports and temporary data folders only."""

import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")

from backend import config as config_mod  # noqa: E402
from backend import runtime  # noqa: E402
from backend.launcher import app, autostart, instance, winapi  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def free_port(kind=socket.SOCK_STREAM) -> int:
    s = socket.socket(socket.AF_INET, kind)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def env() -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith("PITWALL_")}
    e.update(PITWALL_NO_DIALOGS="1", PITWALL_NO_TRAY="1", PYTHONIOENCODING="utf-8")
    return e


def launch(data: Path, web: int, udp: int, *extra: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "backend.launcher", "--background", "--data-dir", str(data),
         "--web-port", str(web), "--udp-port", str(udp), *extra],
        cwd=ROOT, env=env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
    )


def run_exit(data: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "backend.launcher", "--exit", "--data-dir", str(data)],
                          cwd=ROOT, env=env(), capture_output=True, text=True, timeout=60)


@pytest.fixture()
def ports():
    return free_port(), free_port(socket.SOCK_DGRAM)


# ------------------------------------------------------------------ primitives
def test_mutex_name_scoped_by_data_dir(tmp_path):
    a, b = instance.mutex_name(tmp_path / "a"), instance.mutex_name(tmp_path / "b")
    assert a != b and a.startswith("Local\\HorizonPitwall-")
    assert instance.mutex_name(tmp_path / "a") == instance.mutex_name(Path(str(tmp_path / "A")))


def test_mutex_single_owner_across_threads(tmp_path):
    name = instance.mutex_name(tmp_path)
    first = winapi.NamedMutex(name)
    assert first.owned
    seen = {}

    def other():
        m = winapi.NamedMutex(name)
        seen["owned"] = m.owned
        m.release()

    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen["owned"] is False
    first.release()
    t = threading.Thread(target=other)
    t.start()
    t.join()
    assert seen["owned"] is True


def test_instance_file_is_not_trusted_without_matching_server(tmp_path):
    # A foreign HTTP server on the recorded URL is never accepted as Pitwall.
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"app": "Something Else", "build": {"server_instance_id": "x"}}).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_port}/"
        instance.write_instance(tmp_path, url=url, server_instance_id="x", exe="test")
        assert runtime.probe(url) is None
        assert instance.verify(instance.read_instance(tmp_path)) is None
        # Dead PID is rejected before any network access.
        d = instance.read_instance(tmp_path)
        d["pid"] = 0x7FFFFFF0
        assert instance.verify(d) is None
        # Non-loopback URLs are never probed.
        d = dict(instance.read_instance(tmp_path), url="http://192.0.2.1:8080/")
        assert instance.verify(d) is None
    finally:
        srv.shutdown()


def test_remove_instance_only_removes_own_record(tmp_path):
    instance.write_instance(tmp_path, url="http://127.0.0.1:1/", server_instance_id="mine", exe="x")
    instance.remove_instance(tmp_path, "someone-else")
    assert instance.read_instance(tmp_path) is not None
    instance.remove_instance(tmp_path, "mine")
    assert instance.read_instance(tmp_path) is None


def test_request_exit_without_owner(tmp_path):
    assert instance.request_exit(tmp_path, timeout=1) is False


def test_autostart_uses_test_key():
    key = r"Software\HorizonPitwallTest\Run"
    exe = r"C:\Program Files\Some Folder\HorizonPitwall.exe"
    try:
        assert autostart.current(key) is None
        assert autostart.refresh_if_moved(exe, key) is False  # never enables by itself
        autostart.enable(exe, key)
        assert autostart.current(key) == f'"{exe}" --background'
        assert autostart.refresh_if_moved(r"D:\New Place\HorizonPitwall.exe", key) is True
        assert "New Place" in autostart.current(key)
        autostart.disable(key)
        assert autostart.current(key) is None
        autostart.disable(key)  # idempotent
    finally:
        import winreg

        for k in (key, r"Software\HorizonPitwallTest"):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, k)
            except OSError:
                pass


def test_parse_keeps_data_dir_for_config():
    own, rest = app.parse(["--background", "--data-dir", "X", "--web-port", "9"])
    assert own.background and own.data_dir == "X"
    assert rest == ["--web-port", "9", "--data-dir", "X"]


def test_config_read_from_data_folder_not_cwd(tmp_path, monkeypatch):
    (tmp_path / "pitwall.config.json").write_text(json.dumps({"web_port": 18999}), encoding="utf-8")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "pitwall.config.json").write_text(json.dumps({"web_port": 1}), encoding="utf-8")
    monkeypatch.chdir(cwd)
    own, rest = app.parse(["--data-dir", str(tmp_path)])
    cfg = app.load_config(own, rest)
    assert cfg.web_port == 18999 and cfg.data_path == tmp_path


# ------------------------------------------------------------------ runtime
def test_runtime_ready_identity_and_stop(tmp_path, ports):
    web, udp = ports
    cfg = config_mod.load_file(None, data_dir=str(tmp_path), web_port=web, udp_port=udp)
    rt = runtime.PitwallRuntime(cfg)
    rt.start_background()
    try:
        assert rt.wait_ready(20)
        body = runtime.probe(rt.url)
        assert body["build"]["server_instance_id"] == rt.instance_id
    finally:
        assert rt.stop(15)
    assert runtime.port_free("127.0.0.1", web) is None
    assert runtime.udp_port_free("127.0.0.1", udp) is None


# ------------------------------------------------------------------ process level
def test_launch_singleton_and_exit(tmp_path, ports):
    web, udp = ports
    data = tmp_path / "data with spaces ü"
    p1 = launch(data, web, udp)
    try:
        url = instance.wait_for_owner(data, 40)
        assert url == f"http://127.0.0.1:{web}/"
        # Second (and third, concurrent) launch reuses the owner and exits 0.
        p2, p3 = launch(data, web, udp), launch(data, web, udp)
        assert p2.wait(60) == 0 and p3.wait(60) == 0
        assert p1.poll() is None
        r = run_exit(data)
        assert r.returncode == 0, r.stdout + r.stderr
        assert p1.wait(30) == 0
    finally:
        if p1.poll() is None:
            p1.kill()
    assert instance.read_instance(data) is None
    assert runtime.port_free("127.0.0.1", web) is None


def test_simultaneous_cold_launches_one_owner(tmp_path, ports):
    web, udp = ports
    data = tmp_path / "d"
    procs = [launch(data, web, udp) for _ in range(3)]
    try:
        assert instance.wait_for_owner(data, 40)
        time.sleep(2)
        running = [p for p in procs if p.poll() is None]
        assert len(running) == 1
        for p in procs:
            if p not in running:
                assert p.returncode == 0, p.stdout.read()
        assert run_exit(data).returncode == 0
        assert running[0].wait(30) == 0
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def test_web_port_in_use_by_other_program(tmp_path, ports):
    web, udp = ports
    s = socket.socket()
    s.bind(("127.0.0.1", web))
    s.listen()
    try:
        p = launch(tmp_path, web, udp)
        out, _ = p.communicate(timeout=60)
        assert p.returncode == app.EXIT_PORT
        assert f"port {web}" in out
    finally:
        s.close()


def test_udp_port_in_use(tmp_path, ports):
    web, udp = ports
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", udp))
    try:
        p = launch(tmp_path, web, udp)
        out, _ = p.communicate(timeout=60)
        assert p.returncode == app.EXIT_PORT
        assert f"{udp} (UDP)" in out
    finally:
        s.close()


def test_bad_config_exits_clearly(tmp_path, ports):
    (tmp_path / "pitwall.config.json").write_text('{"stale_after_ms": "soon"}', encoding="utf-8")
    p = launch(tmp_path, *ports)
    try:
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == app.EXIT_CONFIG
    assert "stale_after_ms" in out and str(tmp_path) in out
    assert run_exit(tmp_path).returncode == app.EXIT_NOT_RUNNING  # --exit still works


def test_missing_ui_exits_clearly(tmp_path, ports):
    e = env()
    e["PITWALL_UI_DIST"] = str(tmp_path / "nope")
    p = subprocess.run([sys.executable, "-m", "backend.launcher", "--background", "--data-dir", str(tmp_path / "d"),
                        "--web-port", str(ports[0]), "--udp-port", str(ports[1])],
                       cwd=ROOT, env=e, capture_output=True, text=True, timeout=60)
    assert p.returncode == app.EXIT_UI
    assert "dashboard files are missing" in p.stderr


def test_unwritable_data_folder(tmp_path, ports):
    f = tmp_path / "file"
    f.write_text("x")
    p = launch(f / "sub", *ports)  # parent is a file: cannot create the folder
    out, _ = p.communicate(timeout=60)
    assert p.returncode == app.EXIT_DATA
    assert "cannot write" in out


def test_killed_owner_is_replaced(tmp_path, ports):
    # A crashed owner leaves instance.json behind; the OS frees its mutex, so the next
    # launch takes over instead of trusting the stale file.
    web, udp = ports
    data = tmp_path / "d"
    p1 = launch(data, web, udp)
    p2 = None
    try:
        assert instance.wait_for_owner(data, 40)
        first_pid = instance.read_instance(data)["pid"]  # venv python.exe is a stub; this is the interpreter
        p1.kill()
        p1.wait(10)
        assert instance.read_instance(data) is not None
        assert instance.verify(instance.read_instance(data)) is None
        deadline = time.monotonic() + 10
        while runtime.port_free("127.0.0.1", web) and time.monotonic() < deadline:
            time.sleep(0.2)
        p2 = launch(data, web, udp)
        url = instance.wait_for_owner(data, 40)
        assert url and instance.read_instance(data)["pid"] != first_pid
        assert run_exit(data).returncode == 0
        assert p2.wait(30) == 0
    finally:
        for p in (p1, p2):
            if p is not None and p.poll() is None:
                p.kill()
