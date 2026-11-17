"""Source/console entry point: UDP receiver + API + UI + recorder; Ctrl+C stops it cleanly.

The Windows executable uses backend.launcher instead; both share backend.runtime.
"""

from __future__ import annotations

import logging
import sys
import webbrowser
from typing import Optional

from . import config as config_mod
from .runtime import PitwallRuntime, port_free, setup_logging


def main(argv: Optional[list[str]] = None) -> int:
    try:
        cfg = config_mod.load(argv)
    except config_mod.ConfigError as e:
        print(f"[pitwall] configuration error in {e}", file=sys.stderr)
        return 2
    if getattr(cfg, "show_paths", False):
        for k, v in cfg.paths().items():
            print(f"{k:12} {v}")
        return 0
    setup_logging(cfg)
    log = logging.getLogger("pitwall")

    host = cfg.effective_web_host
    err = port_free(host, cfg.web_port)
    if err:
        log.error("web port %s:%s is not available (%s). Use --web-port to pick another.", host, cfg.web_port, err)
        return 3

    rt = PitwallRuntime(cfg)
    rt.log_banner()

    def opened() -> None:
        try:
            webbrowser.open(rt.url)
        except Exception as e:  # noqa: BLE001 - the server keeps running without a browser
            log.warning("could not open a browser (%s); open %s manually", e, rt.url)

    rt.serve_foreground(on_ready=opened if cfg.open_browser else None)
    if rt.hub.receiver_state == "error":
        log.error("receiver error: %s", rt.hub.receiver_error)
    log.info("stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
