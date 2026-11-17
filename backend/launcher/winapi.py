"""Small ctypes wrappers for the Windows APIs the launcher needs.

Named kernel objects are created in the session-local namespace (``Local\\``) with the
default security descriptor of the creating token, so only processes of the same user
session (plus SYSTEM/administrators) can open them. That is the lifecycle IPC boundary:
no network endpoint can stop or restart the app.
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional

IS_WINDOWS = sys.platform == "win32"

ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 0x102
SYNCHRONIZE = 0x00100000
EVENT_MODIFY_STATE = 0x0002
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
STILL_ACTIVE = 259

MB_OK = 0x0
MB_ICONERROR = 0x10
MB_ICONWARNING = 0x30
MB_ICONINFORMATION = 0x40
MB_SETFOREGROUND = 0x10000

if IS_WINDOWS:
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)
    _sh32 = ctypes.WinDLL("shell32", use_last_error=True)

    _k32.CreateMutexW.restype = wintypes.HANDLE
    _k32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.CreateEventW.restype = wintypes.HANDLE
    _k32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.OpenEventW.restype = wintypes.HANDLE
    _k32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
    _k32.SetEvent.argtypes = [wintypes.HANDLE]
    _k32.WaitForSingleObject.restype = wintypes.DWORD
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _k32.ReleaseMutex.argtypes = [wintypes.HANDLE]
    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    _u32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    _k32.AttachConsole.argtypes = [wintypes.DWORD]
    _sh32.ShellExecuteExW.argtypes = [ctypes.c_void_p]


def dialogs_enabled() -> bool:
    # Automated tests set PITWALL_NO_DIALOGS=1 so failures end with an exit code instead
    # of a modal box nobody can click.
    return IS_WINDOWS and os.environ.get("PITWALL_NO_DIALOGS") != "1"


def message_box(text: str, title: str = "Horizon Pitwall", kind: str = "error") -> None:
    flags = {"error": MB_ICONERROR, "warning": MB_ICONWARNING, "info": MB_ICONINFORMATION}[kind] | MB_OK | MB_SETFOREGROUND
    if dialogs_enabled():
        _u32.MessageBoxW(None, text, title, flags)
    elif sys.stderr is not None:
        print(f"[{kind}] {text}", file=sys.stderr, flush=True)


class NamedMutex:
    """Session-local singleton. ``owned`` is True for the first process only; the OS
    releases the mutex if the owner dies, so a crash never leaves a stale lock."""

    def __init__(self, name: str) -> None:
        self.name = name
        ctypes.set_last_error(0)  # CreateMutexW leaves the error untouched for a new mutex
        self.handle = _k32.CreateMutexW(None, True, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self.owned = ctypes.get_last_error() != ERROR_ALREADY_EXISTS
        if not self.owned:
            # Another live process holds it, or its owner died (abandoned): try to take
            # ownership without waiting so a crashed owner does not block forever.
            r = _k32.WaitForSingleObject(self.handle, 0)
            self.owned = r in (WAIT_OBJECT_0, 0x80)  # 0x80 = WAIT_ABANDONED

    def release(self) -> None:
        if self.handle:
            if self.owned:
                _k32.ReleaseMutex(self.handle)
            _k32.CloseHandle(self.handle)
            self.handle = None
            self.owned = False


class NamedEvent:
    def __init__(self, name: str, create: bool = True) -> None:
        self.name = name
        if create:
            self.handle = _k32.CreateEventW(None, True, False, name)
        else:
            self.handle = _k32.OpenEventW(SYNCHRONIZE | EVENT_MODIFY_STATE, False, name)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def set(self) -> None:
        _k32.SetEvent(self.handle)

    def wait(self, timeout_ms: int) -> bool:
        return _k32.WaitForSingleObject(self.handle, timeout_ms) == WAIT_OBJECT_0

    def close(self) -> None:
        if self.handle:
            _k32.CloseHandle(self.handle)
            self.handle = None


def process_alive(pid: int) -> bool:
    h = _k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        return bool(_k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        _k32.CloseHandle(h)


def is_admin() -> bool:
    try:
        return bool(_sh32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def attach_parent_console() -> bool:
    """Reconnect stdout/stderr to the terminal that started this windowed executable."""
    if not IS_WINDOWS or not _k32.AttachConsole(0xFFFFFFFF):  # ATTACH_PARENT_PROCESS
        return False
    sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
    sys.stderr = sys.stdout
    return True


def alloc_console() -> None:
    if IS_WINDOWS and _k32.AllocConsole():
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", errors="replace", buffering=1)  # noqa: SIM115
        sys.stderr = sys.stdout


class SHELLEXECUTEINFOW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND), ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR), ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR), ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE), ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR), ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD), ("hIcon", wintypes.HANDLE), ("hProcess", wintypes.HANDLE),
    ]


def run_elevated(exe: str, params: str, timeout_ms: int = 600_000) -> Optional[int]:
    """Start ``exe`` with the UAC prompt and wait for it. Returns its exit code, or None
    if the user declined the prompt or it could not start."""
    SEE_MASK_NOCLOSEPROCESS = 0x40
    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = "runas"
    info.lpFile = exe
    info.lpParameters = params
    info.nShow = 0
    if not _sh32.ShellExecuteExW(ctypes.byref(info)) or not info.hProcess:
        return None
    try:
        _k32.WaitForSingleObject(info.hProcess, timeout_ms)
        code = wintypes.DWORD()
        _k32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        _k32.CloseHandle(info.hProcess)
