"""Build the standalone Windows x64 release of Horizon Pitwall.

    python tools/build_exe.py [--build-ui] [--tag vX.Y.Z] [--out dist-release]

Steps: check that every version string agrees, optionally rebuild the UI, embed build
metadata (backend/_build_info.json), run PyInstaller (packaging/HorizonPitwall.spec),
add the user-facing files, verify the folder against an allowlist and a forbidden list,
then write

    <out>/HorizonPitwall-<version>-windows-x64.zip   one top folder HorizonPitwall-<version>/
    <out>/SHA256SUMS.txt
    <out>/HorizonPitwall-<version>-release-manifest.json

Run it from the build virtual environment (requirements-build.txt). It never uploads
or publishes anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.buildinfo import backend_digest, read_ui_manifest  # noqa: E402
from backend.config import APP_NAME, APP_VERSION  # noqa: E402

BUILD = ROOT / "build"
PYI_DIST, PYI_WORK = BUILD / "pyi-dist", BUILD / "pyi-work"
BUILD_INFO = ROOT / "backend" / "_build_info.json"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"

REQUIRED = [
    "HorizonPitwall.exe", "HorizonPitwallService.exe", "README.txt", "LICENSE.txt", "THIRD_PARTY_NOTICES.txt",
    "pitwall.config.example.json", "_internal/frontend/dist/index.html", "_internal/frontend/dist/build-manifest.json",
    "_internal/backend/_build_info.json", "_internal/packaging/HorizonPitwall.ico",
]
TOP_ALLOWED = {"HorizonPitwall.exe", "HorizonPitwallService.exe", "_internal", "README.txt", "LICENSE.txt",
               "THIRD_PARTY_NOTICES.txt", "pitwall.config.example.json"}
# Never shipped: VCS/dependency/cache folders, project tests and tools, user data, secrets.
FORBIDDEN_ANYWHERE = {".git", "node_modules", ".venv", ".venv-build", ".claude", "__pycache__", ".pytest_cache"}
FORBIDDEN_INTERNAL = {"tests", "tools", "fixtures", "data", "recordings", "captures", "legacy", "docs"}
FORBIDDEN_SUFFIXES = {".sqlite3", ".sqlite3-wal", ".sqlite3-shm", ".log", ".jsonl", ".partial", ".env", ".pem", ".key", ".pfx"}
FORBIDDEN_NAMES = {"pitwall.config.json", "instance.json", ".env", "_build_info.json.tmp"}
# Distribution names whose license text is bundled in THIRD_PARTY_NOTICES.txt.
RUNTIME_DISTS = ["fastapi", "starlette", "pydantic", "pydantic-core", "annotated-types", "typing-extensions", "typing-inspection",
                 "anyio", "idna", "uvicorn", "h11", "click", "colorama", "websockets", "pystray", "pillow", "six",
                 "pywin32", "psutil", "pyinstaller"]


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def safe_rmtree(p: Path, inside: Path) -> None:
    """Remove a build output folder, but only one strictly inside ``inside``."""
    p, inside = p.resolve(), inside.resolve()
    if p == inside or inside not in p.parents:
        raise SystemExit(f"refusing to delete {p}: not inside {inside}")
    if p.exists():
        shutil.rmtree(p)


def check_versions(tag: str | None) -> dict:
    problems = []
    pkg = json.loads((ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))["version"]
    ui = read_ui_manifest(ROOT / "frontend" / "dist") or {}
    if pkg != APP_VERSION:
        problems.append(f"frontend/package.json version {pkg} != backend APP_VERSION {APP_VERSION}")
    if ui.get("ui_version") != APP_VERSION:
        problems.append(f"frontend/dist was built for {ui.get('ui_version')}, not {APP_VERSION}: rebuild the UI (--build-ui)")
    if tag is not None and tag != f"v{APP_VERSION}":
        problems.append(f"tag {tag} does not match version v{APP_VERSION}")
    if problems:
        raise SystemExit("version check failed:\n  " + "\n  ".join(problems))
    return ui


def build_ui() -> None:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise SystemExit("npm not found; install Node.js or build the UI separately")
    subprocess.run([npm, "ci"], cwd=ROOT / "frontend", check=True)
    subprocess.run([npm, "run", "build"], cwd=ROOT / "frontend", check=True)


def tool_version(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0])
    if not exe:
        return None
    try:
        return subprocess.run([exe, *cmd[1:]], capture_output=True, text=True, timeout=30).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def write_build_info(ui: dict) -> dict:
    # The UI bundle is rebuilt by the release job (new timestamps), so it does not count as a source change.
    dirty = bool(git("status", "--porcelain", "--untracked-files=no", "--", ".", ":(exclude)frontend/dist"))
    info = {
        "app": APP_NAME, "app_version": APP_VERSION,
        "commit": git("rev-parse", "HEAD") or None, "source_dirty": dirty,
        "source_digest": backend_digest(),
        "ui_build_id": ui.get("ui_build_id"),
        "built_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "architecture": "windows-x64",
        "python": platform.python_version(),
        "pyinstaller": md.version("pyinstaller"),
        "node": tool_version(["node", "--version"]),
    }
    BUILD_INFO.write_text(json.dumps(info, indent=2) + "\n", encoding="utf-8")
    return info


def write_version_info() -> None:
    nums = tuple(int(x) for x in APP_VERSION.split(".")[:3]) + (0,)
    VERSION_INFO.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Horizon Pitwall contributors'),
      StringStruct('FileDescription', 'Horizon Pitwall - Forza Horizon 6 telemetry dashboard'),
      StringStruct('FileVersion', '{APP_VERSION}'),
      StringStruct('InternalName', 'HorizonPitwall'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'HorizonPitwall.exe'),
      StringStruct('ProductName', 'Horizon Pitwall'),
      StringStruct('ProductVersion', '{APP_VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")


def license_text(dist: md.Distribution) -> str:
    for f in dist.files or []:
        name = f.name.upper()
        if name.startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")) or "LICENSE" in str(f).upper().split("/")[-2:-1]:
            try:
                return Path(dist.locate_file(f)).read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
    return ""


def third_party_notices() -> str:
    out = ["Horizon Pitwall bundles the following third-party components.", "",
           f"Python {platform.python_version()} runtime - Python Software Foundation License (https://docs.python.org/3/license.html)", ""]
    missing = []
    for name in RUNTIME_DISTS:
        try:
            d = md.distribution(name)
        except md.PackageNotFoundError:
            missing.append(name)
            continue
        meta = d.metadata
        lic = meta.get("License-Expression") or meta.get("License") or ""
        if len(lic) > 80 or not lic:
            lic = next((c.split("::")[-1].strip() for c in meta.get_all("Classifier") or [] if c.startswith("License ::")), lic[:80])
        out += ["=" * 78, f"{meta['Name']} {d.version} - {lic}", meta.get("Home-page") or "", "", license_text(d) or "(license text not included in the package metadata)", ""]
    if missing:
        raise SystemExit(f"missing runtime packages for notices: {missing}")
    out += ["=" * 78, "Dashboard UI: React and react-dom - MIT License, Copyright (c) Meta Platforms, Inc. and affiliates.",
            "https://github.com/facebook/react/blob/main/LICENSE", ""]
    return "\n".join(out) + "\n"


def run_pyinstaller() -> Path:
    for p in (PYI_DIST, PYI_WORK):
        safe_rmtree(p, BUILD)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--distpath", str(PYI_DIST), "--workpath", str(PYI_WORK), str(ROOT / "packaging" / "HorizonPitwall.spec")],
                   cwd=ROOT, check=True)
    return PYI_DIST / "HorizonPitwall"


def add_user_files(folder: Path) -> None:
    (folder / "README.txt").write_text((ROOT / "packaging" / "README.txt").read_text(encoding="utf-8").replace("{version}", APP_VERSION),
                                       encoding="utf-8", newline="\r\n")
    shutil.copy2(ROOT / "LICENSE", folder / "LICENSE.txt")
    (folder / "THIRD_PARTY_NOTICES.txt").write_text(third_party_notices(), encoding="utf-8", newline="\r\n")
    shutil.copy2(ROOT / "pitwall.config.example.json", folder / "pitwall.config.example.json")


def verify_folder(folder: Path) -> list[str]:
    problems = [f"missing {r}" for r in REQUIRED if not (folder / r).exists()]
    problems += [f"unexpected top-level entry {p.name}" for p in folder.iterdir() if p.name not in TOP_ALLOWED]
    for p in folder.rglob("*"):
        parts = p.relative_to(folder).parts
        low = [x.lower() for x in parts]
        if set(low) & FORBIDDEN_ANYWHERE:
            problems.append(f"forbidden path {'/'.join(parts)}")
        elif low[0] == "_internal" and len(low) > 1 and (low[1] in FORBIDDEN_INTERNAL or (low[1] == "backend" and len(low) > 2 and low[2] == "tests")):
            problems.append(f"forbidden path {'/'.join(parts)}")
        elif p.is_file() and (p.suffix.lower() in FORBIDDEN_SUFFIXES or p.name.lower() in FORBIDDEN_NAMES):
            problems.append(f"forbidden file {'/'.join(parts)}")
    emb = json.loads((folder / "_internal" / "backend" / "_build_info.json").read_text(encoding="utf-8"))
    if emb.get("app_version") != APP_VERSION or len(emb.get("source_digest") or "") != 64:
        problems.append("embedded build info is wrong")
    return problems


def make_zip(folder: Path, out: Path) -> Path:
    top = f"HorizonPitwall-{APP_VERSION}"
    z = out / f"HorizonPitwall-{APP_VERSION}-windows-x64.zip"
    tmp = z.with_suffix(".zip.partial")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                zf.write(p, f"{top}/{p.relative_to(folder).as_posix()}")
    tmp.replace(z)
    with zipfile.ZipFile(z) as zf:
        bad = zf.testzip()
        tops = {n.split("/", 1)[0] for n in zf.namelist()}
        if bad or tops != {top} or any(".." in n or n.startswith("/") for n in zf.namelist()):
            raise SystemExit(f"zip verification failed: bad={bad} tops={tops}")
    return z


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build-ui", action="store_true", help="run npm ci + npm run build first")
    ap.add_argument("--tag", help="release tag to check against the version (vX.Y.Z)")
    ap.add_argument("--out", default=str(ROOT / "dist-release"))
    a = ap.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("build on Windows x64")
    if a.build_ui:
        build_ui()
    ui = check_versions(a.tag)
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(exist_ok=True)
    try:
        info = write_build_info(ui)
        write_version_info()
        folder = run_pyinstaller()
    finally:
        # Build-only files never stay in the source tree.
        BUILD_INFO.unlink(missing_ok=True)
        VERSION_INFO.unlink(missing_ok=True)
    add_user_files(folder)
    problems = verify_folder(folder)
    if problems:
        raise SystemExit("release folder check failed:\n  " + "\n  ".join(problems[:50]))
    z = make_zip(folder, out)
    files = sorted(p for p in folder.rglob("*") if p.is_file())
    manifest = {
        "app": APP_NAME, "version": APP_VERSION, "build": info, "zip": z.name, "zip_sha256": sha256(z), "zip_bytes": z.stat().st_size,
        "files": [{"path": p.relative_to(folder).as_posix(), "bytes": p.stat().st_size, "sha256": sha256(p)} for p in files],
    }
    mf = out / f"HorizonPitwall-{APP_VERSION}-release-manifest.json"
    mf.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (out / "SHA256SUMS.txt").write_text(f"{sha256(z)}  {z.name}\n{sha256(mf)}  {mf.name}\n", encoding="utf-8", newline="\n")
    total = sum(p.stat().st_size for p in files)
    print(f"built {z} ({z.stat().st_size / 2**20:.1f} MB zip, {total / 2**20:.1f} MB unpacked, {len(files)} files)")
    print(f"commit {info['commit']} dirty={info['source_dirty']} digest {info['source_digest'][:16]}")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"zip={z}\nmanifest={mf}\nsums={out / 'SHA256SUMS.txt'}\nversion={APP_VERSION}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
