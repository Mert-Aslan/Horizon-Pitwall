"""Install, run and remove the real Windows Service from the release ZIP.

    python tools/test_service_exe.py --zip <zip> --allow-system-changes

This changes the machine: it registers the HorizonPitwall service, copies the release to
%ProgramFiles%\\Horizon Pitwall and creates %ProgramData%\\HorizonPitwall. It is meant
for disposable machines (the GitHub Actions Windows runner, Windows Sandbox, a VM) and
must run elevated. It refuses to start if a Pitwall service, program folder or service
data folder already exists, and removes everything it created at the end.
"""

from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import runtime  # noqa: E402
from backend.config import APP_VERSION  # noqa: E402
from backend.launcher import winapi  # noqa: E402
from backend.service import manage  # noqa: E402
from tools.test_exe import Http, Sender, free_port  # noqa: E402

results: list[dict] = []


def check(name: str, fn) -> bool:
    try:
        detail, ok = fn(), True
    except Exception as e:  # noqa: BLE001
        detail, ok = f"{type(e).__name__}: {e}", False
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""), flush=True)
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", default=str(ROOT / "dist-release" / f"HorizonPitwall-{APP_VERSION}-windows-x64.zip"))
    ap.add_argument("--allow-system-changes", action="store_true")
    a = ap.parse_args()
    if not a.allow_system_changes:
        raise SystemExit("refusing: pass --allow-system-changes (installs a Windows service; use a disposable machine)")
    if not winapi.is_admin():
        raise SystemExit("run elevated")
    pd, pf = manage.program_data_root(), manage.install_root()
    if manage.state() is not None or pf.exists() or pd.exists():
        raise SystemExit(f"refusing: an existing Pitwall service or folder was found ({pf}, {pd})")

    tmp = Path(tempfile.mkdtemp(prefix="pitwall-svc-test-"))
    with zipfile.ZipFile(a.zip) as zf:
        zf.extractall(tmp)
    app = tmp / f"HorizonPitwall-{APP_VERSION}"
    exe = str(app / "HorizonPitwall.exe")
    web, udp = free_port(), free_port(socket.SOCK_DGRAM)
    pd.mkdir(parents=True)
    (pd / "pitwall.config.json").write_text(json.dumps({"web_port": web, "udp_port": udp}), encoding="utf-8")
    url = f"http://127.0.0.1:{web}/"
    env = {"PITWALL_NO_DIALOGS": "1"}

    def svc(*args: str) -> int:
        import os

        return subprocess.run([exe, "service", *args], env={**os.environ, **env}, timeout=180).returncode

    try:
        ok = check("service install (copies to Program Files, starts)", lambda: (svc("install") == 0 and manage.state() == "running") or _fail(manage.state()))
        if not ok:
            diagnostics(pd)
            raise SystemExit(1)
        check("installed under Program Files with marker", lambda: (pf / manage.APP_EXE).exists() and (pf / manage.INSTALL_MARKER).exists() or _fail(pf))

        def identity():
            b = Http(url).json("GET", "/api/v1/build")
            assert b["frozen"] and b["app_version"] == APP_VERSION
            return b["build_id"]

        check("service dashboard answers with release identity", identity)

        def account():
            import psutil

            out = subprocess.run(["sc", "queryex", manage.SERVICE_NAME], capture_output=True, text=True).stdout
            pid = int(out.split("PID")[1].split(":")[1].split()[0])
            user = psutil.Process(pid).username()
            assert user.upper().endswith("LOCAL SERVICE"), user
            return user

        check("runs as LocalService", account)

        def acl():
            out = subprocess.run(["icacls", str(pd)], capture_output=True, text=True).stdout
            assert "Users" not in out.replace("LOCAL SERVICE", ""), out
            return " ".join(out.split()[:12])

        check("ProgramData ACL excludes ordinary users", acl)

        def telemetry():
            Sender(udp).burst(60)
            time.sleep(0.5)
            assert "4242" in json.dumps(Http(url).json("GET", "/api/v1/telemetry/latest"))
            return None

        check("service receives UDP telemetry", telemetry)

        def launcher_defers():
            import os

            # Isolated user data folder whose settings use the service's ports: if the service
            # were not detected, the launcher would hit busy ports instead of serving 8080/5607.
            local = tmp / "user local appdata"
            (local / "HorizonPitwall").mkdir(parents=True)
            (local / "HorizonPitwall" / "pitwall.config.json").write_text(json.dumps({"web_port": web, "udp_port": udp}), encoding="utf-8")
            p = subprocess.Popen([exe, "--background"], env={**os.environ, **env, "LOCALAPPDATA": str(local)})
            try:
                code = p.wait(60)
            finally:
                if p.poll() is None:
                    p.kill()
            assert code == 0, code
            assert not (local / "HorizonPitwall" / "run" / "instance.json").exists()
            return None

        check("tray launcher uses the running service instead of a second receiver", launcher_defers)
        check("service stop", lambda: svc("stop") == 0 and manage.state() == "stopped" or _fail(manage.state()))
        check("service ports released after stop", lambda: runtime.port_free("127.0.0.1", web) is None or _fail("busy"))
        check("service start", lambda: svc("start") == 0 and runtime.probe(url) is not None or _fail(manage.state()))
        check("service status", lambda: svc("status") == 0 or _fail("status"))
        check("service uninstall keeps data", lambda: svc("uninstall") == 0 and manage.state() is None and not pf.exists()
              and (pd / "data").exists() or _fail((manage.state(), pf.exists())))
    finally:
        if any(not r["ok"] for r in results):
            diagnostics(pd)
        if manage.state() is not None:
            svc("uninstall")
        if pf.exists() and (pf / manage.INSTALL_MARKER).exists():
            manage.safe_rmtree(pf, must_be_in=pf.parent, marker=manage.INSTALL_MARKER)
        if pd.exists():
            manage.safe_rmtree(pd, must_be_in=pd.parent)
        shutil.rmtree(tmp, ignore_errors=True)
    failed = [r for r in results if not r["ok"]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed")
    Path(a.zip).with_name("service-test-report.json").write_text(json.dumps(results, indent=1) + "\n", encoding="utf-8")
    return 1 if failed else 0


_dumped = False


def diagnostics(pd: Path) -> None:
    """Print what the service left behind, for CI logs."""
    global _dumped
    if _dumped:
        return
    _dumped = True
    print("\n==== diagnostics ====", flush=True)
    for cmd in (["sc", "qc", manage.SERVICE_NAME], ["sc", "queryex", manage.SERVICE_NAME]):
        print(subprocess.run(cmd, capture_output=True, text=True).stdout, flush=True)
    for f in sorted((pd / "data" / "logs").glob("*")) if (pd / "data" / "logs").exists() else []:
        print(f"---- {f}", flush=True)
        print(f.read_text(encoding="utf-8", errors="replace")[-6000:], flush=True)
    ps = ("Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Service Control Manager'; StartTime=(Get-Date).AddMinutes(-15)} "
          "-MaxEvents 15 -ErrorAction SilentlyContinue | Format-List TimeCreated,Id,Message; "
          "Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=(Get-Date).AddMinutes(-15)} -MaxEvents 30 "
          "-ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match 'Python|Pitwall|Application Error|Windows Error' } "
          "| Format-List TimeCreated,ProviderName,Id,Message")
    print(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout[-12000:], flush=True)
    print("==== end diagnostics ====", flush=True)


def _fail(detail):
    raise AssertionError(detail)


if __name__ == "__main__":
    sys.exit(main())
