"""Legacy source package (needs Python on the user's PC). The recommended download is the
standalone Windows package from tools/build_exe.py.

Build a self-contained source release folder + zip of Horizon Pitwall.

    python tools/build_release.py [--out release] [--wheels]

The release contains only what a user needs to run the app: backend/, the built UI
(frontend/dist), the launcher, the two .cmd files, requirements.txt, README and the
install guide. No tests, tools, recordings, logs or node_modules.

--wheels additionally runs ``pip download`` for requirements.txt into release/.../wheels
so "Install Pitwall.cmd" can install without internet. That step downloads packages
from pypi.org (Windows 64-bit wheels for the running Python version); without it the
installer downloads them itself.

A MANIFEST.json lists every file with its SHA-256, the app version and the backend
digest that /api/v1/build will report, so a running copy can be matched to its release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.buildinfo import backend_digest, read_ui_manifest  # noqa: E402
from backend.config import APP_VERSION  # noqa: E402

INCLUDE_FILES = ["forza_horizon_6_telemetry.py", "requirements.txt", "README.md", "LICENSE", "pitwall.config.example.json",
                 "Start Pitwall.cmd", "Install Pitwall.cmd", "docs/INSTALL.md", "docs/PROTOCOL.md", "docs/STORAGE_FORMAT.md"]
INCLUDE_DIRS = ["backend", "frontend/dist"]
SKIP = {"__pycache__", ".pytest_cache"}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def copy_tree(src: Path, dst: Path) -> None:
    for p in src.rglob("*"):
        if any(part in SKIP for part in p.parts) or p.suffix == ".pyc":
            continue
        if p.is_file():
            t = dst / p.relative_to(src)
            t.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, t)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "release"))
    ap.add_argument("--wheels", action="store_true", help="also download wheels for an offline install (uses the internet)")
    a = ap.parse_args()
    ui = read_ui_manifest(ROOT / "frontend" / "dist")
    if not ui:
        raise SystemExit("frontend/dist/build-manifest.json missing: run `npm run build` in frontend first")
    if ui.get("ui_version") != APP_VERSION:
        raise SystemExit(f"UI version {ui.get('ui_version')} != app version {APP_VERSION}: rebuild the UI")
    name = f"HorizonPitwall-{APP_VERSION}"
    out = Path(a.out)
    target = out / name
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for d in INCLUDE_DIRS:
        copy_tree(ROOT / d, target / d)
    for f in INCLUDE_FILES:
        (target / f).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, target / f)
    if a.wheels:
        subprocess.run([sys.executable, "-m", "pip", "download", "-r", str(ROOT / "requirements.txt"), "-d", str(target / "wheels"),
                        "--only-binary=:all:", "--platform", "win_amd64",
                        "--python-version", f"{sys.version_info.major}.{sys.version_info.minor}"], check=True)
        # Compiled wheels (pydantic_core) only fit this Python version; the installer reads it.
        (target / "wheels" / "PYTHON_VERSION.txt").write_text(f"{sys.version_info.major}.{sys.version_info.minor}", encoding="ascii")
    files = sorted(p for p in target.rglob("*") if p.is_file())
    manifest = {
        "app": "Horizon Pitwall",
        "app_version": APP_VERSION,
        "backend_digest": backend_digest(target / "backend"),
        "ui": ui,
        "offline_wheels": a.wheels,
        "files": {p.relative_to(target).as_posix(): sha256(p) for p in files},
    }
    (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    zip_path = out / f"{name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(target.rglob("*")):
            if p.is_file():
                z.write(p, Path(name) / p.relative_to(target))
    size = zip_path.stat().st_size / 2**20
    print(f"release folder: {target}")
    print(f"zip: {zip_path} ({size:.1f} MiB, {len(files) + 1} files, backend digest {manifest['backend_digest'][:10]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
