# Optional Windows Service mode

Normally Pitwall runs while you are signed in, from the tray. Service mode is for a PC that
should receive and record telemetry without anyone signed in, for example a dedicated
second PC next to a console. It is optional and nothing about it happens unless you run the
commands below.

## What it does

`HorizonPitwall.exe service install`, run from the extracted release folder:

1. asks for administrator rights (UAC prompt) if needed;
2. copies the release folder to `%ProgramFiles%\Horizon Pitwall` (a marker file identifies
   it as a Pitwall installation; an unrelated folder of the same name is never replaced);
3. creates `%ProgramData%\HorizonPitwall` with these permissions only: SYSTEM and
   Administrators full control, the service account (LOCAL SERVICE) modify. Ordinary users
   cannot read or change the service's recordings or settings;
4. registers the service **HorizonPitwall** ("Horizon Pitwall"), which runs the same
   `HorizonPitwall.exe` with the internal option `--service-host`; automatic start, running as
   `NT AUTHORITY\LocalService` (no administrator rights, no network credentials);
5. sets bounded recovery: restart after 10 s, then after 60 s, then stay stopped; the
   failure count resets after a day;
6. starts it and waits until the dashboard answers.

The service shows no window or tray icon (services cannot show UI). Open its dashboard at
<http://127.0.0.1:8080/>, or start `HorizonPitwall.exe` normally: when the service is
running, the tray launcher opens the service's dashboard instead of starting a second
receiver.

## Commands

Run these from the extracted release folder (`status`, `start` and `stop` also work from
`%ProgramFiles%\Horizon Pitwall`; `uninstall` must run from the release folder because a
program cannot delete its own folder):

| Command | Effect |
|---|---|
| `HorizonPitwall.exe service install` | Install or upgrade (replaces the program files, keeps data and settings), then start |
| `HorizonPitwall.exe service install --copy-user-data` | As above, and copy your own recordings and car names into the service data folder once |
| `HorizonPitwall.exe service status` | Show state and whether the dashboard answers (no admin needed) |
| `HorizonPitwall.exe service stop` / `start` | Stop or start the service |
| `HorizonPitwall.exe service uninstall` | Stop and remove the service and `%ProgramFiles%\Horizon Pitwall`; data is kept |
| `HorizonPitwall.exe service uninstall --delete-data` | Also delete `%ProgramData%\HorizonPitwall` (cannot be undone) |

Results are shown in the terminal you ran the command from, or in a message box.
Exit code 0 means success.

## Data and settings

| What | Location |
|---|---|
| Settings | `%ProgramData%\HorizonPitwall\pitwall.config.json` (created as `{}` on first install, never overwritten) |
| Recordings, car names, logs | `%ProgramData%\HorizonPitwall\data\` |

The service never uses your personal data folder (`%LOCALAPPDATA%\HorizonPitwall`).
`--copy-user-data` copies (never moves) your recordings and car names, and only into an
empty service folder; if the service already has recordings, nothing is merged. Your
originals stay where they are.

Edit the settings file as an administrator, then run `service stop` and `service start`.
The keys are the same as in [INSTALL.md](INSTALL.md#settings); `data_dir` is ignored.

## Ports and the tray app

The service and a tray Pitwall cannot both receive on the same UDP port. While the service
runs, starting `HorizonPitwall.exe` opens the service dashboard and exits. If the service
is installed but stopped, the tray app starts normally. If you give the service other ports
than your tray app and start both, each receives on its own port.

## Security notes

- The dashboard listens on `127.0.0.1` only unless you set `lan` in the service settings.
- Controls such as start/stop of the service are Windows operations (SCM, administrator
  rights); nothing in the dashboard or the network can install, stop or reconfigure the
  service.
- Logs contain no secrets; there are no access tokens.

## Upgrading the service

Extract the new release, then run `HorizonPitwall.exe service install` from the new folder.
The service is stopped, the program files are replaced, settings and data are kept, and the
service is started again.

## Removing the service

```powershell
HorizonPitwall.exe service uninstall
```

This removes the service registration and `%ProgramFiles%\Horizon Pitwall` and keeps
`%ProgramData%\HorizonPitwall`. Add `--delete-data` to remove that as well.

## Testing

`tools/test_service_exe.py` installs, exercises and removes the service from a release ZIP.
It changes the machine, refuses to run without `--allow-system-changes` and refuses if a
Pitwall service or folder already exists. It runs in CI on disposable GitHub-hosted Windows
runners; run it yourself only in Windows Sandbox or a VM.
