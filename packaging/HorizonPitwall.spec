# PyInstaller spec: one folder, two executables sharing one runtime.
#   HorizonPitwall.exe         windowed tray launcher (normal use)
#   HorizonPitwallService.exe  console-subsystem Windows Service host (optional)
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
    + ["win32timezone", "servicemanager", "win32serviceutil", "win32service", "win32event", "pystray._win32"]
)
datas = [
    (str(ROOT / "frontend" / "dist"), "frontend/dist"),
    (str(ROOT / "backend" / "_build_info.json"), "backend"),
    (ICON, "packaging"),
]
excludes = ["tkinter", "pytest", "IPython", "matplotlib", "numpy", "pandas", "setuptools", "pip",
            # Pillow is only used to draw the tray icon; image codecs and font/colour engines are not needed.
            "PIL._avif", "PIL.AvifImagePlugin", "PIL._webp", "PIL.WebPImagePlugin", "PIL._imagingtk", "PIL.ImageTk",
            "PIL._imagingcms", "PIL.ImageCms", "PIL.ImageQt", "PIL._imagingft"]

common = dict(pathex=[str(ROOT)], hiddenimports=hidden, excludes=excludes, noarchive=False)

a_app = Analysis([str(ROOT / "packaging" / "entry_launcher.py")], datas=datas, **common)  # noqa: F821
a_svc = Analysis([str(ROOT / "packaging" / "entry_service.py")], datas=[], **common)  # noqa: F821

exe_app = EXE(  # noqa: F821
    PYZ(a_app.pure),  # noqa: F821
    a_app.scripts, [], exclude_binaries=True, name="HorizonPitwall", icon=ICON, version=VERSION_FILE,
    console=False, upx=False, uac_admin=False,
)
exe_svc = EXE(  # noqa: F821
    PYZ(a_svc.pure),  # noqa: F821
    a_svc.scripts, [], exclude_binaries=True, name="HorizonPitwallService", icon=ICON, version=VERSION_FILE,
    console=True, upx=False, uac_admin=False,
)
coll = COLLECT(  # noqa: F821
    exe_app, a_app.binaries, a_app.datas,
    exe_svc, a_svc.binaries, a_svc.datas,
    name="HorizonPitwall", upx=False,
)
