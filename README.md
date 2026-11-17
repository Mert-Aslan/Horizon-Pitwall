# Horizon Pitwall

A local telemetry dashboard for **Forza Horizon 6** on Windows. It receives the game's
"Data Out" UDP stream on your PC and shows it in a browser tab: a live cockpit for a second
monitor, manual session recording, replay, a channel-by-channel analysis view and timed
performance runs.

Everything stays on your computer. Pitwall has no account, sends nothing to the internet
and does not modify the game.

Version 0.8.0. Tested with a real Forza Horizon 6 session on Windows 11.

## Download and start

1. Open the [Releases page](https://github.com/Mert-Aslan/Horizon-Pitwall/releases) and
   download `HorizonPitwall-<version>-windows-x64.zip`.
2. Right-click the ZIP, choose **Extract All**, and extract it to a folder you keep
   (for example `Documents\Horizon Pitwall`).
3. Double-click **`HorizonPitwall.exe`** in the extracted folder. Your browser opens
   <http://127.0.0.1:8080/> once Pitwall is ready, and a Pitwall icon appears in the
   notification area (system tray).

No Python, Node.js or installer is needed. The executable is not code-signed, so Windows
SmartScreen may ask for confirmation the first time (**More info › Run anyway**). Each
release lists SHA-256 checksums for the ZIP.

Starting `HorizonPitwall.exe` again while it is running just opens the dashboard of the
copy that is already running.

## Set up the game

In Forza Horizon 6 go to **Settings › HUD and Gameplay**:

| Setting | Value |
|---|---|
| Data Out | On |
| Data Out IP Address | `127.0.0.1` (game and Pitwall on the same PC) |
| Data Out IP Port | `5607` |

The game only sends data while you drive. In menus, pause, rewind and replays the stream
stops and Pitwall shows **Data stale**, which is expected.

To receive from a console or another PC on your network, see
[Settings](docs/INSTALL.md#settings) (`udp_host`).

## The tray icon

Right-click the Pitwall icon in the notification area:

- **Open Dashboard** (also a left-click on the icon)
- **Open Data Folder** and **Open Logs**
- **Start with Windows**: off by default; when on, Pitwall starts in the tray at sign-in
  without opening a browser
- **Exit**: stops Pitwall cleanly. A recording that is in progress is finished and saved.

Closing the browser tab does not stop Pitwall; use **Exit**. From a terminal,
`HorizonPitwall.exe --exit` does the same.

## What it does

- **Live**: rev band, gear and speed, throttle, brake and steering, four-wheel tyre
  temperature, slip and suspension, a 20-second trace, power, torque and boost. A focus
  view keeps only the main readouts.
- **Recording**: the **Record** button saves every received packet until you press
  **Stop**. Recording runs in the background app, so it continues if the browser tab is
  closed. If Pitwall or the PC stops unexpectedly, the unfinished recording is recovered
  on the next start and marked as recovered.
- **Sessions and replay**: a list of recordings with duration, speed summary and notes;
  replay in the same cockpit view at 0.5×, 1× or 2×, with seeking.
- **Telemetry Lab**: plot any channels over a recording, zoom into a range, see statistics,
  a G-G plot, the route from above, and export the selected range as CSV. Automatically
  detected events are labelled as heuristics.
- **Performance runs**: 0–100 km/h, 100–200 km/h and 100–0 km/h braking, measured from the
  telemetry. Runs with data gaps, rewinds or a car change are rejected with the reason.
  Two runs can be compared.
- **Garage**: the game identifies cars only by a number. Pitwall ships no car-name list;
  you name a car once and the name is used everywhere. Name lists can be exported and
  imported.
- **Diagnostics**: connection state, packet counters, build identity and a short local
  packet capture for troubleshooting.
- **Demo mode**: synthetic data, clearly labelled, only when you choose it. Pitwall never
  shows invented data on its own.

## Where your data is

Recordings, car names, settings and logs are kept in your user profile, not in the program
folder:

```
%LOCALAPPDATA%\HorizonPitwall
```

Replacing or deleting the program folder does not touch this data. Recordings are never
deleted automatically (about 27 MB per hour of driving). Back up by exiting Pitwall and
copying the folder.

## Updating and removing

- **Update**: exit Pitwall, extract the new ZIP to a new folder and start the new
  `HorizonPitwall.exe`. Your data is kept. If **Start with Windows** was on, it now points
  to the new folder. Keep the old folder until you are happy with the new version.
- **Remove**: exit Pitwall, turn off **Start with Windows** if you enabled it, then delete
  the program folder. Delete `%LOCALAPPDATA%\HorizonPitwall` only if you also want to
  delete your recordings.

## Optional: Windows Service mode

For a PC that should receive telemetry without anyone signed in, Pitwall can also run as a
Windows Service. This is optional, needs administrator rights and is only set up when you
run `HorizonPitwall.exe service install`. Most users do not need it. See
[docs/SERVICE.md](docs/SERVICE.md).

## Troubleshooting

| Symptom | What to check |
|---|---|
| **Waiting for data** | Data Out is off, the IP or port does not match, or you are not driving yet. Diagnostics shows whether any packets arrive. |
| "The telemetry port 5607 (UDP) is already in use" at start | Another program receives on that port (another telemetry tool, or a second Pitwall). Close it, or change `udp_port` and the game's port. |
| "The dashboard port 8080 is already in use" at start | Another program uses port 8080. Close it or set `web_port`. |
| Pitwall icon is grey | The background server stopped. Pitwall restarts it automatically a few times; otherwise use **Restart Pitwall** in the menu and check **Open Logs**. |
| **Unexpected packet format** | The sender is not Forza Horizon 6, or another program writes to the same port. |

More: [docs/INSTALL.md](docs/INSTALL.md). Logs: `%LOCALAPPDATA%\HorizonPitwall\logs\pitwall.log`.

## Documentation

- [docs/INSTALL.md](docs/INSTALL.md): installing, settings, updating, data and troubleshooting
- [docs/SERVICE.md](docs/SERVICE.md): optional Windows Service mode
- [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md): running from source, tests, building the package
- [docs/RELEASING.md](docs/RELEASING.md): how releases are built and published
- Technical notes (in Turkish): [protocol](docs/PROTOCOL.md), [architecture](docs/ARCHITECTURE.md),
  [storage format](docs/STORAGE_FORMAT.md), [design system](docs/DESIGN_SYSTEM.md),
  [implementation status](docs/IMPLEMENTATION_STATUS.md)

## License

[MIT](LICENSE). The Windows package bundles third-party components; their licenses are in
`THIRD_PARTY_NOTICES.txt` inside the ZIP.

Horizon Pitwall is an independent hobby project. It is not affiliated with or endorsed by
Microsoft, Xbox Game Studios, Playground Games or Turn 10 Studios. Forza Horizon is a
trademark of Microsoft.
