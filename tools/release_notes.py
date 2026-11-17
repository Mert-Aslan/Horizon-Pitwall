"""Write the body of a draft GitHub release from the build outputs.

    python tools/release_notes.py <dist-release folder> <output.md>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import APP_VERSION  # noqa: E402


def main() -> int:
    out_dir, target = Path(sys.argv[1]), Path(sys.argv[2])
    manifest = json.loads((out_dir / f"HorizonPitwall-{APP_VERSION}-release-manifest.json").read_text(encoding="utf-8"))
    sums = (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").strip()
    b = manifest["build"]
    zip_name = manifest["zip"]
    text = f"""Horizon Pitwall {APP_VERSION} for Windows 10/11 (64-bit).

## Install
1. Download `{zip_name}`.
2. Right-click the ZIP > Extract All, to a folder you keep (for example `Documents\\Horizon Pitwall`).
3. Open the extracted folder and double-click `HorizonPitwall.exe`. The dashboard opens in your browser.
4. In Forza Horizon 6: Settings > HUD and Gameplay > Data Out **On**, IP `127.0.0.1`, port `5607`.

No Python or Node.js is needed. The executable is not code-signed, so Windows SmartScreen may
ask for confirmation the first time (More info > Run anyway). Check the download against the
SHA-256 below if in doubt.

Full guide: [docs/INSTALL.md](https://github.com/Mert-Aslan/Horizon-Pitwall/blob/v{APP_VERSION}/docs/INSTALL.md)

## Build
- Commit: `{b['commit']}`
- Built: {b['built_at_utc']} with Python {b['python']}, PyInstaller {b['pyinstaller']}, Node {b.get('node') or 'n/a'}
- Source digest: `{b['source_digest']}`

## SHA-256
```
{sums}
```
"""
    target.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
