# Installing and running Horizon Pitwall (Windows)

Horizon Pitwall runs entirely on your PC. It needs no internet connection and sends no data
anywhere.

## Requirements

- Windows 10 or 11, 64-bit
- Forza Horizon 6 on the same PC, or on a console/PC in the same network
- A current browser (Edge, Chrome or Firefox)

## Install

1. Download `HorizonPitwall-<version>-windows-x64.zip` from the
   [Releases page](https://github.com/Mert-Aslan/Horizon-Pitwall/releases).
2. Optional: check the download. In PowerShell:
   ```powershell
   Get-FileHash .\HorizonPitwall-<version>-windows-x64.zip -Algorithm SHA256
   ```
   The value must match the line for the ZIP in the release's `SHA256SUMS.txt`.
3. Right-click the ZIP › **Extract All** to a folder you keep, for example
   `Documents\Horizon Pitwall`. Do not run it from inside the ZIP. The path may contain
   spaces and non-English characters.
4. Double-click `HorizonPitwall.exe`.

The folder contains:

| File | Purpose |
|---|---|
| `HorizonPitwall.exe` | Starts Pitwall for you (tray icon + browser) |
| `HorizonPitwallService.exe` | Used only by the optional Windows Service mode; do not start it directly |
| `_internal\` | Runtime files; keep it next to the executables |
| `README.txt`, `LICENSE.txt`, `THIRD_PARTY_NOTICES.txt` | Short guide and licenses |
| `pitwall.config.example.json` | Example settings |

The executable is not code-signed. Windows SmartScreen may show "Windows protected your
PC" on first start; choose **More info › Run anyway** if the checksum matched.

## Start, stop, start with Windows

- **Start**: double-click `HorizonPitwall.exe`. The browser opens when the server has
  answered its own health check. Pitwall stays in the notification area.
- **Open again**: left-click the tray icon, or start `HorizonPitwall.exe` again. Only one
  Pitwall runs per Windows user; a second start opens the running one.
- **Stop**: tray menu › **Exit**, or `HorizonPitwall.exe --exit`. An active recording is
  closed and saved first. Signing out or shutting down Windows also stops Pitwall cleanly.
- **Start with Windows**: tray menu › **Start with Windows**. Stored for your user only
  (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`); Pitwall then starts in the tray
  without opening a browser. Off by default.

Command line options of `HorizonPitwall.exe`:

| Option | Effect |
|---|---|
| `--background` | Start in the tray without opening the browser |
| `--exit` | Ask the running Pitwall of this user to exit |
| `--paths` | Show where data, logs and recordings are kept |
| `--debug-console` | Show a console window with the live log |
| `--data-dir <folder>` | Use another data folder (also reads its settings from there) |
| `service install\|start\|stop\|status\|uninstall` | Optional Windows Service mode, see [SERVICE.md](SERVICE.md) |

Exit codes: 0 ok, 1 not running / not responding, 2 invalid settings, 3 port in use,
4 dashboard files missing, 5 did not become ready, 6 data folder not writable.

## Where your data is

| What | Location |
|---|---|
| Data folder | `%LOCALAPPDATA%\HorizonPitwall` |
| Settings | `…\pitwall.config.json` (optional; not created automatically) |
| Logs | `…\logs\pitwall.log` (1 MB × 3 rotating files, times in UTC) |
| Recordings | `…\recordings` (SQLite + compressed JSONL, about 27 MB per hour at 60 Hz) |
| Diagnostic captures | `…\captures` (at most 10 files, cleaned up automatically) |
| Car names | `…\catalog.sqlite3` |

The tray menu opens the data and log folders. Nothing is written to the program folder.
Recordings are never deleted automatically.

**Backup**: exit Pitwall and copy the data folder.

## Settings

Create `%LOCALAPPDATA%\HorizonPitwall\pitwall.config.json` (copy
`pitwall.config.example.json` and keep only what you change), then restart Pitwall.

| Key | Default | Meaning |
|---|---|---|
| `udp_port` | `5607` | Port Pitwall receives on. Must equal the game's Data Out IP Port. Avoid 5200–5300; the game uses that range itself. |
| `udp_host` | `"127.0.0.1"` | Address Pitwall listens on. `127.0.0.1` accepts only this PC. `"0.0.0.0"` also accepts a console or PC on the network; then enter this PC's network address as the game's Data Out IP. |
| `web_port` | `8080` | Port of the dashboard in the browser |
| `lan` | `false` | `true` also serves the dashboard to phones and tablets on your network (read-only for them) |
| `lan_allow_control` | `false` | Let network devices start recordings or demo mode |
| `log_level` | `"INFO"` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

Settings are checked strictly. A wrong type (for example `"lan": "false"` in quotes), an
unknown key or invalid JSON stops the start with a message naming the field, and Pitwall
exits with code 2. Pitwall never changes a port on its own: if a port is taken it tells you
which program uses it and stops.

Windows may ask for firewall permission when `lan` or `udp_host: "0.0.0.0"` is used.
Pitwall does not change firewall rules and never opens anything to the internet.

## Updating

1. Exit Pitwall.
2. Extract the new ZIP to a **new** folder. Keep the old one until the new version works.
3. Start the new `HorizonPitwall.exe`. If Start with Windows was on, it now points to the
   new folder.

Your data folder is shared between versions. Data formats are versioned: an older Pitwall
that finds data written by a newer one does not modify it; it disables recording or car
names, keeps the live view working and writes the reason to the log. Diagnostics ›
*Server build* shows the running version. A browser tab left open on the old version shows
"newer version of this page"; reload it.

## Going back to an older version

Exit Pitwall and start `HorizonPitwall.exe` from the old folder. Data written by newer
versions is left untouched as described above.

## Uninstalling

1. If you enabled **Start with Windows**, turn it off in the tray menu. Then exit Pitwall.
2. If you installed the optional service: `HorizonPitwall.exe service uninstall`
   (see [SERVICE.md](SERVICE.md)).
3. Delete the program folder.
4. Only if you also want to delete recordings and car names: delete
   `%LOCALAPPDATA%\HorizonPitwall`. This cannot be undone.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Nothing happens on double-click | Pitwall may already be running in the tray; left-click its icon. Otherwise run `HorizonPitwall.exe --debug-console` to see the log. |
| "The telemetry port … (UDP) is already in use by …" | Another program receives on that port (another telemetry tool or a Pitwall started from source). Close it, or set `udp_port` and the game's port to another value. |
| "The dashboard port … is already in use by …" | Close that program or set `web_port`. |
| "Horizon Pitwall is already running for this user but does not respond" | Exit it from the tray (or `HorizonPitwall.exe --exit`), then start again. |
| "a setting is invalid" | Fix or delete `pitwall.config.json` as the message says. |
| "The dashboard files are missing" | The ZIP was not extracted completely; extract it again. |
| "cannot write to its data folder" | `%LOCALAPPDATA%` is not writable (policy, full disk) or `--data-dir` points somewhere invalid. |
| **Waiting for data** | Check Data Out settings in the game. Diagnostics › *Received* shows whether packets arrive. |
| **Unexpected packet format** | The sender is not Forza Horizon 6 (for example Forza Motorsport), or another program writes to the port. |
| **Data stale** | The game is in a menu, paused, rewinding or in a replay. |
| Tray icon is grey | The server stopped. Pitwall restarts it up to 3 times in 10 minutes; after that use **Restart Pitwall** and check **Open Logs**. |

When reporting a problem, include `pitwall.log` and the version from Diagnostics.

## Running from source

For developers, or if you prefer not to run a prebuilt executable: see
[DEVELOPMENT.md](DEVELOPMENT.md). The older source package with `Install Pitwall.cmd` /
`Start Pitwall.cmd` still works for that purpose but is no longer the recommended way to
install.
