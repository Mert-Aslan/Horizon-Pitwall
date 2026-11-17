# PyInstaller spec: one folder, one executable.
#   HorizonPitwall.exe              tray launcher (normal use)
#   HorizonPitwall.exe --service-host   only when the optional Windows Service is started by Windows
# Build with tools/build_exe.py, which writes backend/_build_info.json and
# packaging/version_info.txt first.

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
ICON = str(ROOT / "packaging" / "HorizonPitwall.ico")
VERSION_FILE = str(ROOT / "packaging" / "version_info.txt")

hidden = (
    collect_submodules("uvicorn")
    + collect_submodules("websockets")
    + ["win32timezone", "servicemanager", "win32serviceutil", "win32service", "win32event", "pystray._win32",
       # imported inside functions; listed so they are always bundled
       "backend.service.svc", "backend.service.manage", "backend.launcher.tray", "backend.launcher.autostart"]
)
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "backend" / "_build_info.json"), "backend"),
    (ICON, "packaging"),
]
excludes = ["tkinter", "pytest", "IPython", "matplotlib", "numpy", "pandas", "setuptools", "pip",
            # Optional imports of dependencies that Pitwall never uses (YAML log config, bz2/lzma archives).
            "yaml", "bz2", "lzma", "_bz2", "_lzma",
            # Pillow is only used to draw the tray icon; image codecs and font/colour engines are not needed.
            "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL._imagingtk", "PIL.ImageTk",
            "PIL._imagingcms", "PIL.ImageCms", "PIL.ImageQt", "PIL._imagingft"]

a = Analysis([str(ROOT / "packaging" / "entry_launcher.py")], pathex=[str(ROOT)], datas=datas,  # noqa: F821
             hiddenimports=hidden, excludes=excludes, noarchive=False)


def _keep(name: str) -> bool:
    # The Universal C Runtime (api-ms-win-*.dll, ucrtbase.dll) is part of Windows 10 and 11;
    # copies in the program folder are only needed on older Windows, which Pitwall does not support.
    n = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return not (n.startswith("api-ms-win-") or n == "ucrtbase.dll")


a.binaries = [b for b in a.binaries if _keep(b[0])]

exe = EXE(  # noqa: F821
    PYZ(a.pure),  # noqa: F821
    a.scripts, [], exclude_binaries=True, name="HorizonPitwall", icon=ICON, version=VERSION_FILE,
    console=False, upx=False, uac_admin=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="HorizonPitwall", upx=False)  # noqa: F821
