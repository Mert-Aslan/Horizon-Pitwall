"""Horizon Pitwall launcher - Forza Horizon 6 Telemetry & Driving Analysis.

Keeps the original start command working:

    python .\\forza_horizon_6_telemetry.py            # live, UDP 127.0.0.1:5607, UI http://127.0.0.1:8080/
    python .\\forza_horizon_6_telemetry.py --demo     # clearly labelled synthetic data
    python .\\forza_horizon_6_telemetry.py --help
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.main import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
