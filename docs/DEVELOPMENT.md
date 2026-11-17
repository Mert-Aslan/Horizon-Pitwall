# Development

## Layout

| Path | Contents |
|---|---|
| `backend/` | Python: UDP receiver and decoder (`telemetry/`), recorder (`recording/`), analysis (`analysis/`), FastAPI app (`api/`) |
| `backend/runtime.py` | Shared start/ready/stop lifecycle used by all entry points |
| `backend/launcher/` | Tray launcher (`HorizonPitwall.exe`): singleton, instance file, exit event, autostart, tray icon |
| `backend/service/` | Optional Windows Service: management commands (`manage.py`) and SCM host (`svc.py`) |
| `frontend/` | React + TypeScript UI (Vite). The built bundle in `frontend/dist` is committed |
| `packaging/` | PyInstaller spec, entry scripts, icon, the README shipped in the ZIP |
| `tools/` | Build, package test, fixture and load-test scripts |
| `tests/` | pytest suite |

## Run from source

Requires Python 3.11+ (CI and releases use 3.13) and, for UI work, Node.js 24.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python .\forza_horizon_6_telemetry.py
```

The dashboard is at <http://127.0.0.1:8080/>; stop with `Ctrl+C`. `--help` lists the
options. In a source checkout `./pitwall.config.json` is read if present.

To run the tray launcher from source (as the packaged exe does), install the app
requirements and start the module:

```powershell
.\.venv\Scripts\python -m pip install -r requirements-app.txt
.\.venv\Scripts\python -m backend.launcher --data-dir "$env:TEMP\pitwall-dev"
```

Use a separate `--data-dir` and ports (`--web-port`, `--udp-port`) for development so a
Pitwall you use for driving is not affected. Never send test packets to port 5607 while the
game or your own Pitwall uses it; `tools/send_synthetic.py` refuses it without
`--allow-default-port`.

```powershell
.\.venv\Scripts\python .\forza_horizon_6_telemetry.py --udp-port 15607 --web-port 18080 --data-dir "$env:TEMP\pitwall-dev"
.\.venv\Scripts\python .\tools\send_synthetic.py --port 15607 --seconds 30
```

### UI development

```powershell
cd frontend
npm ci
npm run dev        # http://localhost:5173, API proxied to :8080
npm run build      # writes frontend/dist, served by the backend
```

`frontend/package.json` and `APP_VERSION` in `backend/config.py` must carry the same
version; the package build checks this.

## Tests

```powershell
.\.venv\Scripts\python -m pip install -r requirements-build.txt pytest==9.1.1 httpx==0.28.1
$env:PITWALL_NO_DIALOGS = "1"
.\.venv\Scripts\python -m pytest -q
cd frontend; npm test; npm run typecheck
```

The launcher and service tests start real processes on free ephemeral ports with temporary
data folders, and use a test registry key for autostart. `PITWALL_NO_DIALOGS=1` turns
message boxes into stderr output; `PITWALL_NO_TRAY=1` runs the launcher without a tray icon.

Browser smoke test (Playwright, once `python -m playwright install chromium`):
`python tools/ui_smoke.py`. It starts its own isolated server on ports 18100/15620.

## Build the Windows package

```powershell
python -m venv .venv-build
.\.venv-build\Scripts\python -m pip install -r requirements-build.txt pytest==9.1.1 httpx==0.28.1
.\.venv-build\Scripts\python tools\build_exe.py            # add --build-ui to rebuild the UI first
.\.venv-build\Scripts\python tools\test_exe.py             # end-to-end test of the ZIP
```

`build_exe.py` checks the versions, writes `backend/_build_info.json` (commit, dirty flag,
source digest, tool versions; deleted again after the build), runs PyInstaller with
`packaging/HorizonPitwall.spec`, adds the user files, checks the folder against an
allowlist and a forbidden list (no tests, user data, logs, `.git`, `node_modules`, secrets)
and writes to `dist-release/`:

- `HorizonPitwall-<version>-windows-x64.zip` with a single top folder
- `HorizonPitwall-<version>-release-manifest.json` (every file with size and SHA-256)
- `SHA256SUMS.txt`

`test_exe.py` extracts the ZIP to a temporary path with spaces and non-ASCII characters,
runs the exe with a stripped environment (no Python on `PATH`) and isolated ports, and
checks readiness and identity, assets, WebSocket, UDP, recording, replay frames, Lab, CSV,
car names, single-instance behaviour, `--exit` during a recording, crash recovery and the
error exits. It only stops processes started from its own folder.

`tools/test_service_exe.py` tests the real service and changes the machine; see
[SERVICE.md](SERVICE.md#testing).

Regenerate the icon from the favicon geometry with `python tools/make_icon.py`.

## Source package (legacy)

`tools/build_release.py` builds the older source ZIP that installs a private Python
environment with `Install Pitwall.cmd` and starts it with `Start Pitwall.cmd`. It needs
Python on the user's PC and is kept for people who prefer running from source. The
standalone package above is the recommended download.

## Conventions

- Short, technical comments. Documentation in English for users; some deeper technical
  notes in `docs/` are in Turkish.
- No network access at runtime; no telemetry or update checks.
- Commits use the repository's configured author; CI must pass before a release tag.
