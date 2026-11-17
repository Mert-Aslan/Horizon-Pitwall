Horizon Pitwall {version} for Windows (64-bit)
================================================

Live telemetry dashboard for Forza Horizon 6 "Data Out". Everything runs on this PC.

Start
-----
1. Double-click HorizonPitwall.exe. Your browser opens the dashboard
   (http://127.0.0.1:8080/). Pitwall keeps running in the notification area
   (tray icon); use its menu to reopen the dashboard or exit.
2. In Forza Horizon 6: Settings > HUD and Gameplay
     Data Out             On
     Data Out IP Address  127.0.0.1
     Data Out IP Port     5607

Keep this whole folder together; HorizonPitwall.exe needs the _internal folder next
to it. No Python, Node.js or installer is required.

Where your data is
------------------
%LOCALAPPDATA%\HorizonPitwall   recordings, car names, logs, pitwall.config.json
Deleting or replacing this program folder does not touch that data.

More
----
Tray menu: Open Dashboard, Open Data Folder, Open Logs, Start with Windows, Exit.
HorizonPitwall.exe --exit       stop the running copy
HorizonPitwall.exe --paths      show where data and logs are kept
Settings, troubleshooting and the optional Windows Service mode:
https://github.com/Mert-Aslan/Horizon-Pitwall/blob/main/docs/INSTALL.md

License: MIT (LICENSE.txt). Bundled components: THIRD_PARTY_NOTICES.txt.
Horizon Pitwall is not affiliated with Microsoft, Xbox Game Studios or Playground Games.
