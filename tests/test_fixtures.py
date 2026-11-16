import json
from pathlib import Path

import pytest

from backend.telemetry import decode as dec

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "synthetic"
MANIFEST = json.loads((FIX / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", MANIFEST, ids=lambda c: c["file"])
def test_fixture_outcome(case):
    data = (FIX / case["file"]).read_bytes()
    assert len(data) == case["length"]
    r = dec.decode(data)
    if case["expected"] == "accepted":
        assert r.ok
        n, d, q = dec.normalize(r.raw)
        json.dumps({"n": n, "d": d, "q": q, "raw": dec.json_safe_raw(r.raw)}, allow_nan=False)
        assert n["car"]["ordinal"] < 0  # fixtures never carry a real car id
    else:
        assert not r.ok and r.reject_reason == case["expected"]
