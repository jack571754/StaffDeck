import asyncio
import sys
import types

import pytest

from app.core.platform_compat import (
    install_windows_loop_exception_handler,
    patch_windows_proactor_connection_lost,
)


def test_patch_windows_proactor_connection_lost_idempotent() -> None:
    # Multiple calls should not raise or double-patch unexpectedly
    patch_windows_proactor_connection_lost()
    patch_windows_proactor_connection_lost()


def test_patched_connection_lost_survives_hostile_socket_shutdown(monkeypatch) -> None:
    """The connection-lost callback must never take down the event loop.

    An earlier revision assigned to the socket's read-only ``shutdown``
    attribute, raising AttributeError inside the loop callback. Whatever the
    failure mode of ``sock.shutdown()``, cleanup must still run.
    """
    try:
        from asyncio import proactor_events
    except ImportError:  # pragma: no cover - POSIX without the proactor module
        pytest.skip("asyncio.proactor_events unavailable")

    monkeypatch.setattr(sys, "platform", "win32")
    from app.core import platform_compat

    monkeypatch.setattr(platform_compat, "_PATCHED", False)
    saved = proactor_events._ProactorBasePipeTransport._call_connection_lost
    try:
        patch_windows_proactor_connection_lost()
        patched = proactor_events._ProactorBasePipeTransport._call_connection_lost
        assert patched is not saved

        closed: list[bool] = []
        lost: list[object] = []

        class _HostileSock:
            def fileno(self) -> int:
                return 5

            def shutdown(self, how: int) -> None:
                raise AttributeError(
                    "'socket' object attribute 'shutdown' is read-only"
                )

            def close(self) -> None:
                closed.append(True)

        class _Protocol:
            def connection_lost(self, exc: object) -> None:
                lost.append(exc)

        transport = types.SimpleNamespace(
            _called_connection_lost=False,
            _protocol=_Protocol(),
            _sock=_HostileSock(),
            _server=None,
        )

        patched(transport, None)

        assert lost == [None]
        assert closed == [True]
        assert transport._sock is None
        assert transport._called_connection_lost is True
    finally:
        proactor_events._ProactorBasePipeTransport._call_connection_lost = saved


def test_install_windows_loop_exception_handler(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")

    loop = asyncio.new_event_loop()
    try:
        passed_exceptions: list[dict[str, object]] = []

        def custom_orig_handler(l, context):
            passed_exceptions.append(context)

        loop.set_exception_handler(custom_orig_handler)
        install_windows_loop_exception_handler(loop)

        # Call with ConnectionResetError -> should be suppressed
        handler = loop.get_exception_handler()
        assert handler is not None
        handler(loop, {"message": "lost connection", "exception": ConnectionResetError()})
        assert len(passed_exceptions) == 0

        # Call with OSError(10054) -> should be suppressed
        os_err = OSError()
        os_err.winerror = 10054
        handler(loop, {"message": "lost connection", "exception": os_err})
        assert len(passed_exceptions) == 0

        # Call with another exception (e.g. ValueError) -> should be passed to original handler
        handler(loop, {"message": "other error", "exception": ValueError("boom")})
        assert len(passed_exceptions) == 1
        assert isinstance(passed_exceptions[0]["exception"], ValueError)
    finally:
        loop.close()
