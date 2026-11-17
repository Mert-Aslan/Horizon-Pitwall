"""Windows Service host (HorizonPitwallService.exe), started only by the Service Control
Manager after an explicit ``HorizonPitwall.exe service install``.

The body is ``run_body`` so it can be exercised without the SCM. Startup is reported
as START_PENDING until the server answers its own health check, then RUNNING. A worker
that fails to start or dies exits the process with a non-zero code so the configured,
bounded SCM recovery actions apply.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path
from typing import Callable, Optional

from .. import runtime
from . import manage

log = logging.getLogger("pitwall.service")


def run_body(stop: threading.Event, report_running: Callable[[], None], root: Optional[Path] = None) -> int:
    cfg = manage.load_service_config(root)
    runtime.setup_logging(cfg, console=False)
    log.info("event=service_starting data=%s", cfg.data_path)
    err = runtime.port_free(cfg.effective_web_host, cfg.web_port)
    if err:
        log.error("event=service_port_busy web port %s is not available (%s)", cfg.web_port, err)
        return 3
    rt = runtime.PitwallRuntime(cfg)
    rt.log_banner()
    rt.start_background()
    if not rt.wait_ready(60):
        log.error("event=service_not_ready")
        rt.stop()
        return 4
    report_running()
    log.info("event=service_running url=%s", rt.url)
    while not stop.wait(2.0):
        if not rt.alive:
            log.error("event=service_worker_died")
            rt.stop()
            return 5
    log.info("event=service_stopping")
    rt.stop(timeout=25)
    log.info("event=service_stopped")
    return 0


def _bootstrap_log(text: str) -> None:
    """Last-resort log when the configured log cannot be opened yet."""
    try:
        d = manage.program_data_root() / "data" / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with (d / "service-error.log").open("a", encoding="utf-8") as f:
            f.write(text.rstrip() + "\n")
    except OSError:
        pass


def _service_class():
    import servicemanager
    import win32service
    import win32serviceutil

    class PitwallService(win32serviceutil.ServiceFramework):
        _svc_name_ = manage.SERVICE_NAME
        _svc_display_name_ = manage.DISPLAY_NAME
        _svc_description_ = manage.DESCRIPTION

        def __init__(self, args) -> None:
            super().__init__(args)
            self.stop_event = threading.Event()

        def SvcStop(self) -> None:  # noqa: N802 - pywin32 API
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING, waitHint=30_000)
            self.stop_event.set()

        def SvcRun(self) -> None:  # noqa: N802
            self.ReportServiceStatus(win32service.SERVICE_START_PENDING, waitHint=60_000)
            try:
                code = run_body(self.stop_event, lambda: self.ReportServiceStatus(win32service.SERVICE_RUNNING))
            except BaseException:  # noqa: BLE001 - must reach a log before the process ends
                import traceback

                text = traceback.format_exc()
                log.error("event=service_crashed\n%s", text)
                _bootstrap_log(text)
                servicemanager.LogErrorMsg(f"Horizon Pitwall service failed to start:\n{text[-1500:]}")
                code = 6
            if code != 0:
                servicemanager.LogErrorMsg(f"Horizon Pitwall stopped with error {code}; see the service log.")
                logging.shutdown()
                os._exit(code)  # process exit -> SCM failure actions (bounded restart)
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)

    return PitwallService


def main() -> int:
    import servicemanager

    if len(sys.argv) > 1:
        print("HorizonPitwallService.exe is started by Windows. Use: HorizonPitwall.exe service install|start|stop|status|uninstall")
        return 2
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(_service_class())
    servicemanager.StartServiceCtrlDispatcher()
    return 0
