"""Car identity with hysteresis (finding D-2).

Real sessions show valid packets carrying ``CarOrdinal = 0`` for a few seconds at a time
(``4156 -> 0 -> 4156``). What 0 means is not documented; it is *not* assumed to be a
car. The raw ordinal is never altered; this tracker derives a display/identity state:

  unknown                  nothing confirmed yet
  candidate                a new non-zero ordinal is being observed
  confirmed                ``confirmed_ordinal`` is the stable live identity
  temporarily_unavailable  packets currently carry 0/None; the previous confirmed
                           identity is kept but flagged as not live-verified
  unavailable              0/None for longer than ``unavailable_after_s``

Only a confirmed A -> B transition is reported as a car change. Thresholds are
provisional (synthetic fixtures; to be revisited with real captures).
"""

from __future__ import annotations

from typing import Optional

CONFIRM_SAMPLES = 20
CONFIRM_SECONDS = 0.3
UNAVAILABLE_AFTER_S = 10.0


class CarIdentity:
    def __init__(self, *, confirm_samples: int = CONFIRM_SAMPLES, confirm_s: float = CONFIRM_SECONDS, unavailable_after_s: float = UNAVAILABLE_AFTER_S) -> None:
        self.confirm_samples = confirm_samples
        self.confirm_s = confirm_s
        self.unavailable_after_s = unavailable_after_s
        self.reset()

    def reset(self) -> None:
        self.status = "unknown"
        self.confirmed: Optional[int] = None
        self.candidate: Optional[int] = None
        self._cand_n = 0
        self._cand_since = 0.0
        self._zero_since: Optional[float] = None
        self.zero_periods = 0

    @staticmethod
    def _is_absent(ordinal: Optional[float]) -> bool:
        return ordinal is None or ordinal == 0

    def update(self, ordinal: Optional[float], now: float) -> Optional[tuple[str, str]]:
        """Feed one accepted sample. Returns an (event_type, detail) when something worth
        logging happened: ``car_identified`` (first confirmation), ``car_change`` (confirmed
        A -> B) or ``car_unavailable`` (absent for longer than the threshold)."""
        if self._is_absent(ordinal):
            self.candidate, self._cand_n = None, 0
            if self._zero_since is None:
                self._zero_since = now
                self.zero_periods += 1
            if now - self._zero_since >= self.unavailable_after_s:
                if self.status != "unavailable":
                    self.status = "unavailable"
                    return ("car_unavailable", f"ordinal absent for {now - self._zero_since:.0f}s (last confirmed {self.confirmed})")
            elif self.status != "unavailable":
                self.status = "temporarily_unavailable" if self.confirmed is not None else "unknown"
            return None

        self._zero_since = None
        o = int(ordinal)
        if o == self.confirmed:
            self.candidate, self._cand_n = None, 0
            self.status = "confirmed"
            return None
        if o != self.candidate:
            self.candidate, self._cand_n, self._cand_since = o, 0, now
        self._cand_n += 1
        if self._cand_n >= self.confirm_samples and now - self._cand_since >= self.confirm_s:
            prev, self.confirmed = self.confirmed, o
            self.candidate, self._cand_n = None, 0
            self.status = "confirmed"
            return ("car_identified", str(o)) if prev is None else ("car_change", f"{prev} -> {o}")
        self.status = "candidate"
        return None

    def as_dict(self, raw_ordinal: Optional[float]) -> dict:
        return {
            "status": self.status,
            "confirmed_ordinal": self.confirmed,
            "candidate_ordinal": self.candidate,
            "raw_ordinal": raw_ordinal,
            "live_verified": self.status == "confirmed",
        }
