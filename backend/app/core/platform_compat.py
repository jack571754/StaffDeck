from __future__ import annotations

import asyncio
import logging
import socket
import sys
from typing import Any

logger = logging.getLogger("staffdeck.platform")

_PATCHED = False


def patch_windows_proactor_connection_lost() -> None:
    """Patch Windows ProactorEventLoop connection lost error.

    On Windows, when a remote client abruptly closes a socket (e.g., browser
    reload, SSE disconnect, health check probe termination),
    _ProactorBasePipeTransport._call_connection_lost invokes
    self._sock.shutdown(socket.SHUT_RDWR) without catching ConnectionResetError
    [WinError 10054]. This unhandled exception bubbles up to the event loop's
    exception handler, flooding stderr with tracebacks and skipping socket cleanup.
    """
    global _PATCHED
    if _PATCHED or sys.platform != "win32":
        return

    try:
        import asyncio.proactor_events

        def _safe_call_connection_lost(self: Any, exc: Exception | None) -> None:
            if getattr(self, "_called_connection_lost", False):
                return
            protocol = getattr(self, "_protocol", None)
            if protocol is not None:
                try:
                    protocol.connection_lost(exc)
                except (OSError, RuntimeError):
                    pass
            sock = getattr(self, "_sock", None)
            if sock is not None:
                if hasattr(sock, "shutdown") and getattr(sock, "fileno", lambda: -1)() != -1:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except (AttributeError, ConnectionResetError, OSError):
                        # AttributeError: never let an unexpected socket shape
                        # (or a future revision assigning to a read-only
                        # attribute) take down the event loop callback.
                        pass
                try:
                    sock.close()
                except OSError:
                    pass
                self._sock = None
            server = getattr(self, "_server", None)
            if server is not None:
                detach = getattr(server, "_detach", None)
                if callable(detach):
                    try:
                        detach(self)
                    except TypeError:
                        try:
                            detach()
                        except (TypeError, AttributeError):
                            pass
                    except AttributeError:
                        pass
                self._server = None
            self._called_connection_lost = True

        asyncio.proactor_events._ProactorBasePipeTransport._call_connection_lost = (
            _safe_call_connection_lost
        )
        _PATCHED = True
    except (ImportError, AttributeError) as exc:  # pragma: no cover
        logger.debug("Failed to patch windows proactor connection lost: %s", exc)





def install_windows_loop_exception_handler(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Install an event loop exception handler to silence spurious WinError 10054 logs."""
    if sys.platform != "win32":
        return

    try:
        target_loop = loop if loop is not None else asyncio.get_running_loop()
    except RuntimeError:
        try:
            target_loop = asyncio.get_event_loop()
        except RuntimeError:
            return

    orig_handler = target_loop.get_exception_handler()

    def _handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError) or (
            isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054
        ):
            return
        if orig_handler is not None:
            orig_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    target_loop.set_exception_handler(_handler)
