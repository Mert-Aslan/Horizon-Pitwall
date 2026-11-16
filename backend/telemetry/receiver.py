"""UDP receiver thread. It only receives and hands bytes to the hub; no disk or network
work happens here. Bind errors are raised synchronously from ``start`` and recorded in
the hub so the UI can show them (no half-running service)."""

from __future__ import annotations

import logging
import socket
import threading
from typing import Optional

from .hub import TelemetryHub

log = logging.getLogger("pitwall.receiver")

WSAEMSGSIZE = 10040


class UdpReceiver:
    def __init__(self, hub: TelemetryHub, host: str, port: int) -> None:
        self.hub = hub
        self.host = host
        self.port = port
        self._sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def start(self) -> bool:
        self._stop.clear()
        self.hub.set_receiver_state("starting", address=self.address)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):  # Windows: refuse to share the port
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
            sock.bind((self.host, self.port))
        except OSError as e:
            sock.close()
            msg = f"cannot bind UDP {self.address}: {e.strerror or e}"
            log.error(msg)
            self.hub.set_receiver_state("error", error=msg, address=self.address)
            return False
        sock.settimeout(0.25)
        self._sock = sock
        self.port = sock.getsockname()[1]  # resolves port 0 in tests
        self.hub.set_receiver_state("listening", address=self.address)
        log.info("listening on UDP %s", self.address)
        self._thread = threading.Thread(target=self._run, name="udp-receiver", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        sock = self._sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows reports ICMP port-unreachable on UDP sockets; harmless here.
                self.hub.count("socket_resets")
                continue
            except OSError as e:
                if self._stop.is_set():
                    break
                if getattr(e, "winerror", None) == WSAEMSGSIZE:
                    self.hub.count("oversize_datagrams")
                    continue
                msg = f"UDP receive failed: {e}"
                log.error(msg)
                self.hub.set_receiver_state("error", error=msg)
                break
            try:
                self.hub.ingest(data, addr, origin="live")
            except Exception:  # never let one bad packet kill the receiver
                log.exception("ingest failed")
                self.hub.count("ingest_exceptions")

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._sock = None
        self._thread = None
        if self.hub.receiver_state != "error":
            self.hub.set_receiver_state("stopped")
