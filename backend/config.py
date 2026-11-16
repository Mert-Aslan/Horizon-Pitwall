"""Configuration: defaults < JSON file < command line."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

APP_NAME = "Horizon Pitwall"
APP_VERSION = "0.2.0"
GAME_RESERVED_PORTS = range(5200, 5301)  # the game binds its own socket in this range


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "HorizonPitwall"


@dataclass
class Config:
    udp_host: str = "127.0.0.1"
    udp_port: int = 5607
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    lan: bool = False  # serve the UI on the LAN (opt-in)
    lan_allow_control: bool = False  # let LAN clients change state (demo toggle)
    extra_allowed_hosts: list[str] = field(default_factory=list)
    stale_after_ms: int = 750
    ui_rate_hz: float = 30.0
    raw_rate_hz: float = 4.0
    demo: bool = False
    open_browser: bool = False
    data_dir: str = ""
    log_level: str = "INFO"

    def validate(self) -> None:
        for name in ("udp_port", "web_port"):
            p = getattr(self, name)
            if not (0 <= p <= 65535):
                raise ValueError(f"{name} out of range: {p}")
        if self.udp_port in GAME_RESERVED_PORTS:
            raise ValueError("udp_port 5200-5300 is used by the game itself; choose another port")
        if not (1 <= self.ui_rate_hz <= 60):
            raise ValueError("ui_rate_hz must be within 1..60")
        if not (0.5 <= self.raw_rate_hz <= 10):
            raise ValueError("raw_rate_hz must be within 0.5..10")
        if self.stale_after_ms < 100:
            raise ValueError("stale_after_ms must be >= 100")

    @property
    def effective_web_host(self) -> str:
        return "0.0.0.0" if self.lan and self.web_host in ("127.0.0.1", "localhost") else self.web_host

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    def public(self) -> dict:
        return asdict(self)


def load(argv: Optional[list[str]] = None) -> Config:
    ap = argparse.ArgumentParser(prog="pitwall", description=f"{APP_NAME} - Forza Horizon 6 telemetry & driving analysis")
    ap.add_argument("--config", help="JSON config file (default: ./pitwall.config.json if present)")
    ap.add_argument("--udp-host")
    ap.add_argument("--udp-port", type=int)
    ap.add_argument("--web-host")
    ap.add_argument("--web-port", type=int)
    ap.add_argument("--lan", action="store_true", default=None, help="serve the UI on the local network (opt-in)")
    ap.add_argument("--demo", action="store_true", default=None, help="start in demo mode (synthetic data, clearly labelled)")
    ap.add_argument("--open", dest="open_browser", action="store_true", default=None, help="open the UI in the default browser")
    ap.add_argument("--data-dir")
    ap.add_argument("--log-level")
    args = ap.parse_args(argv)

    cfg = Config()
    path = Path(args.config) if args.config else Path("pitwall.config.json")
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown config keys in {path}: {sorted(unknown)}")
        for k, v in data.items():
            setattr(cfg, k, v)
    elif args.config:
        raise FileNotFoundError(path)

    for k in ("udp_host", "udp_port", "web_host", "web_port", "lan", "demo", "open_browser", "data_dir", "log_level"):
        v = getattr(args, k)
        if v is not None:
            setattr(cfg, k, v)
    cfg.validate()
    return cfg
