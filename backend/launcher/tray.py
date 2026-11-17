"""Notification-area icon and Windows session-end handling for the launcher."""

from __future__ import annotations

import logging
import threading
from typing import Callable

from PIL import Image, ImageDraw, ImageEnhance

from .. import paths

log = logging.getLogger("pitwall.tray")


def load_icon(healthy: bool = True) -> Image.Image:
    ico = paths.resource_root() / "packaging" / "HorizonPitwall.ico"
    try:
        img = Image.open(ico)
        img.size = max(img.ico.sizes())  # type: ignore[attr-defined]
        img = img.convert("RGBA")
    except Exception:  # noqa: BLE001 - fall back to a drawn mark
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        for i, h in enumerate((22, 30, 40, 50)):
            d.rectangle([10 + i * 12, 58 - h, 18 + i * 12, 58], fill=(232, 179, 91) if i == 3 else (220, 223, 226))
    if not healthy:
        img = ImageEnhance.Color(img).enhance(0.0)
        img = ImageEnhance.Brightness(img).enhance(0.6)
    return img


class Tray:
    def __init__(self, *, on_open: Callable[[], None], on_data: Callable[[], None], on_logs: Callable[[], None],
                 autostart_get: Callable[[], bool], autostart_set: Callable[[bool], None],
                 on_restart: Callable[[], None], can_restart: Callable[[], bool], on_exit: Callable[[], None]) -> None:
        import pystray
        from pystray import Menu, MenuItem

        self._pystray = pystray
        menu = Menu(
            MenuItem("Open Dashboard", lambda: on_open(), default=True),
            MenuItem("Open Data Folder", lambda: on_data()),
            MenuItem("Open Logs", lambda: on_logs()),
            Menu.SEPARATOR,
            MenuItem("Start with Windows", lambda: autostart_set(not autostart_get()), checked=lambda _i: autostart_get()),
            MenuItem("Restart Pitwall", lambda: on_restart(), visible=lambda _i: can_restart()),
            Menu.SEPARATOR,
            MenuItem("Exit", lambda: on_exit()),
        )
        self.icon = pystray.Icon("HorizonPitwall", load_icon(True), "Horizon Pitwall", menu)

    def run(self, setup: Callable[[], None]) -> None:
        def _setup(icon) -> None:
            icon.visible = True
            setup()

        self.icon.run(setup=_setup)

    def set_state(self, healthy: bool, title: str) -> None:
        try:
            self.icon.icon = load_icon(healthy)
            self.icon.title = title[:127]
            self.icon.update_menu()
        except Exception:  # noqa: BLE001
            log.debug("tray update failed", exc_info=True)

    def notify(self, text: str) -> None:
        try:
            self.icon.notify(text[:250], "Horizon Pitwall")
        except Exception:  # noqa: BLE001 - notifications are best effort
            log.debug("tray notification failed", exc_info=True)

    def stop(self) -> None:
        self.icon.stop()


def watch_session_end(on_end: Callable[[], None]) -> threading.Thread:
    """Hidden top-level window that receives WM_QUERYENDSESSION / WM_ENDSESSION
    (message-only windows do not get these broadcasts). On logoff or shutdown it calls
    ``on_end`` synchronously; Windows waits a few seconds for the window procedure."""

    def run() -> None:
        import win32api
        import win32con
        import win32gui

        def wndproc(hwnd, msg, wparam, lparam):
            if msg == win32con.WM_QUERYENDSESSION:
                return True
            if msg == win32con.WM_ENDSESSION:
                if wparam:
                    try:
                        on_end()
                    except Exception:  # noqa: BLE001
                        log.exception("session-end shutdown failed")
                return 0
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = wndproc
        wc.lpszClassName = "HorizonPitwallSessionWatcher"
        wc.hInstance = win32api.GetModuleHandle(None)
        atom = win32gui.RegisterClass(wc)
        win32gui.CreateWindow(atom, "Horizon Pitwall", 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
        win32gui.PumpMessages()

    t = threading.Thread(target=run, name="session-end", daemon=True)
    t.start()
    return t
