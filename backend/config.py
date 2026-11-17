"""Configuration: defaults < JSON file < command line.

Every source goes through the same strict validator (``Config.validate``): dataclass
annotations are not enforced at runtime, so a JSON ``"lan": "false"`` (a truthy string)
or ``"udp_port": true`` would otherwise slip through. Problems are collected and reported
together in one ``ConfigError``.

Two different addresses are easy to mix up:
  * ``udp_host`` is where *this app* binds its UDP socket (127.0.0.1 = only this PC,
    0.0.0.0 = also accept packets from a console/PC on the LAN).
  * The address typed into the game's "Data Out IP Address" is the *destination*: the
    IP of the PC running Pitwall (127.0.0.1 on the same PC). 0.0.0.0 is never a valid
    destination.
``lan`` only concerns the web UI (phones/tablets); it does not change UDP reception.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

APP_NAME = "Horizon Pitwall"
APP_VERSION = "0.8.1"
GAME_RESERVED_PORTS = range(5200, 5301)  # the game binds its own socket in this range
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
EQUAL_TIMESTAMP_POLICIES = ("reject", "accept_changed")


class ConfigError(ValueError):
    def __init__(self, problems: list[str], source: str = "configuration") -> None:
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


def default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "HorizonPitwall"


@dataclass
class Config:
    udp_host: str = "127.0.0.1"
    udp_port: int = 5607
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    lan: bool = False  # serve the web UI on the LAN (opt-in); UDP reception is udp_host
    lan_allow_control: bool = False  # let LAN clients change state (demo, recording)
    extra_allowed_hosts: list[str] = field(default_factory=list)
    stale_after_ms: int = 750
    ui_rate_hz: float = 30.0
    raw_rate_hz: float = 4.0
    demo: bool = False
    open_browser: bool = False
    data_dir: str = ""
    log_level: str = "INFO"
    # Byte-identical repeats are rejected. This policy applies to changed packets at the same timestamp.
    equal_timestamp_policy: str = "accept_changed"
    recorder_queue_size: int = 20_000
    diagnostic_capture_max_mb: float = 16.0

    # ------------------------------------------------------------ validation
    def problems(self) -> list[str]:
        p: list[str] = []

        def is_bool(name: str) -> None:
            v = getattr(self, name)
            if not isinstance(v, bool):
                p.append(f"{name} must be true or false (got {v!r})")

        def is_int(name: str, lo: int, hi: int) -> Optional[int]:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                p.append(f"{name} must be a whole number (got {v!r})")
                return None
            if not lo <= v <= hi:
                p.append(f"{name} must be within {lo}..{hi} (got {v})")
                return None
            return v

        def is_num(name: str, lo: float, hi: float) -> None:
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                p.append(f"{name} must be a number (got {v!r})")
            elif not math.isfinite(v):
                p.append(f"{name} must be finite (got {v!r})")
            elif not lo <= v <= hi:
                p.append(f"{name} must be within {lo}..{hi} (got {v})")

        def is_host(name: str) -> None:
            v = getattr(self, name)
            if not isinstance(v, str) or not v.strip():
                p.append(f"{name} must be an IP address or 'localhost' (got {v!r})")
                return
            if v == "localhost":
                return
            try:
                ipaddress.ip_address(v)
            except ValueError:
                p.append(f"{name} must be an IP address or 'localhost' (got {v!r})")

        for b in ("lan", "lan_allow_control", "demo", "open_browser"):
            is_bool(b)
        is_host("udp_host")
        is_host("web_host")
        udp = is_int("udp_port", 1, 65535)
        is_int("web_port", 1, 65535)
        if udp is not None and udp in GAME_RESERVED_PORTS:
            p.append("udp_port 5200-5300 is used by the game itself; choose another port (default 5607)")
        is_num("stale_after_ms", 100, 10_000)
        is_num("ui_rate_hz", 1, 60)
        is_num("raw_rate_hz", 0.5, 10)
        is_int("recorder_queue_size", 1_000, 1_000_000)
        is_num("diagnostic_capture_max_mb", 1, 256)
        hosts = self.extra_allowed_hosts
        if not isinstance(hosts, list) or not all(isinstance(h, str) and h.strip() and " " not in h for h in hosts):
            p.append(f"extra_allowed_hosts must be a list of host names (got {hosts!r})")
        if not isinstance(self.data_dir, str):
            p.append(f"data_dir must be a folder path string (got {self.data_dir!r})")
        if not isinstance(self.log_level, str) or self.log_level.upper() not in LOG_LEVELS:
            p.append(f"log_level must be one of {', '.join(LOG_LEVELS)} (got {self.log_level!r})")
        if self.equal_timestamp_policy not in EQUAL_TIMESTAMP_POLICIES:
            p.append(f"equal_timestamp_policy must be one of {', '.join(EQUAL_TIMESTAMP_POLICIES)} (got {self.equal_timestamp_policy!r})")
        return p

    def validate(self, source: str = "configuration") -> None:
        problems = self.problems()
        if problems:
            raise ConfigError(problems, source)

    @property
    def effective_web_host(self) -> str:
        return "0.0.0.0" if self.lan and self.web_host in ("127.0.0.1", "localhost") else self.web_host

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    def paths(self) -> dict[str, str]:
        d = self.data_path
        return {
            "data_dir": str(d),
            "logs": str(d / "logs" / "pitwall.log"),
            "recordings": str(d / "recordings"),
            "captures": str(d / "captures"),
            "car_catalog": str(d / "catalog.sqlite3"),
        }

    def public(self) -> dict:
        return asdict(self)


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not allowed in the config file")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ConfigError([f"cannot read file: {e.strerror or e}"], str(path)) from e
    try:
        data = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as e:
        raise ConfigError([f"invalid JSON at line {e.lineno} column {e.colno}: {e.msg}"], str(path)) from e
    except ValueError as e:
        raise ConfigError([str(e)], str(path)) from e
    if not isinstance(data, dict):
        raise ConfigError(["the file must contain one JSON object"], str(path))
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError([f"unknown keys {sorted(unknown)}"], str(path))
    return data


def load_file(path: Optional[Path], **overrides: Any) -> Config:
    """Config from an optional JSON file plus explicit overrides (service/launcher use;
    never looks at the current working directory)."""
    cfg = Config()
    source = "defaults"
    if path is not None and path.exists():
        for k, v in _read_json(path).items():
            setattr(cfg, k, v)
        source = str(path)
    for k, v in overrides.items():
        setattr(cfg, k, v)
    if isinstance(cfg.log_level, str):
        cfg.log_level = cfg.log_level.upper()
    cfg.validate(source)
    return cfg


def load(argv: Optional[list[str]] = None, default_config: Optional[Path] = Path("pitwall.config.json")) -> Config:
    ap = argparse.ArgumentParser(prog="pitwall", description=f"{APP_NAME} - Forza Horizon 6 telemetry & driving analysis")
    ap.add_argument("--config", help="JSON config file (default: ./pitwall.config.json if present)")
    ap.add_argument("--udp-host", help="address this app binds for UDP (127.0.0.1 = this PC only; 0.0.0.0 = also LAN senders)")
    ap.add_argument("--udp-port", type=int)
    ap.add_argument("--web-host")
    ap.add_argument("--web-port", type=int)
    ap.add_argument("--lan", action="store_true", default=None, help="serve the web UI on the local network (opt-in; read-only for LAN clients)")
    ap.add_argument("--demo", action="store_true", default=None, help="start in demo mode (synthetic data, clearly labelled)")
    ap.add_argument("--open", dest="open_browser", action="store_true", default=None, help="open the UI in the default browser")
    ap.add_argument("--data-dir", help="folder for logs, recordings and diagnostic captures")
    ap.add_argument("--log-level", choices=[*LOG_LEVELS, *(x.lower() for x in LOG_LEVELS)])
    ap.add_argument("--paths", dest="show_paths", action="store_true", help="print where logs, recordings and the car catalog are kept, then exit")
    args = ap.parse_args(argv)

    cfg = Config()
    path = Path(args.config) if args.config else default_config
    source = "command line"
    if path is not None and path.exists():
        for k, v in _read_json(path).items():
            setattr(cfg, k, v)
        source = f"{path} / command line"
    elif args.config:
        raise ConfigError(["file not found"], str(path))

    for k in ("udp_host", "udp_port", "web_host", "web_port", "lan", "demo", "open_browser", "data_dir", "log_level"):
        v = getattr(args, k)
        if v is not None:
            setattr(cfg, k, v)
    if isinstance(cfg.log_level, str):
        cfg.log_level = cfg.log_level.upper()
    cfg.validate(source)
    cfg.show_paths = bool(args.show_paths)  # type: ignore[attr-defined]  (CLI-only action, not a setting)
    return cfg
