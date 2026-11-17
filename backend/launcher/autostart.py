"""Opt-in "Start with Windows" for the current user (HKCU Run key, no elevation).

The stored command starts Pitwall in the tray without opening a browser. If the user
moves the folder, the next launch from the new location updates the stored path, but
only when start-up was enabled before.
"""

from __future__ import annotations

from typing import Optional

try:
    import winreg
except ImportError:  # non-Windows source checkouts
    winreg = None  # type: ignore[assignment]

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "HorizonPitwall"


def command_for(exe: str) -> str:
    return f'"{exe}" --background'


def current(key_path: str = RUN_KEY) -> Optional[str]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as k:
            v, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return str(v)
    except OSError:
        return None


def enable(exe: str, key_path: str = RUN_KEY) -> None:
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, command_for(exe))


def disable(key_path: str = RUN_KEY) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
    except FileNotFoundError:
        pass


def refresh_if_moved(exe: str, key_path: str = RUN_KEY) -> bool:
    cur = current(key_path)
    if cur is not None and cur != command_for(exe):
        enable(exe, key_path)
        return True
    return False
