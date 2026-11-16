"""Horizon Pitwall launcher - Forza Horizon 6 Telemetry & Driving Analysis.

Keeps the original start command working:

    python .\\forza_horizon_6_telemetry.py            # live, UDP 127.0.0.1:5607, UI http://127.0.0.1:8080/
    python .\\forza_horizon_6_telemetry.py --demo     # clearly labelled synthetic data
    python .\\forza_horizon_6_telemetry.py --help

The original single-file prototype is preserved unchanged in
legacy/forza_horizon_6_telemetry.original.py (SHA-256 f9e5cbf7...388a7).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
