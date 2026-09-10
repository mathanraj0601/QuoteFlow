"""Tiny in-process pub/sub so the pipeline can push live progress to the UI.

The pipeline runs on the poller thread (or a request worker thread); the
WebSocket clients live on the FastAPI event loop. `app.py` binds the loop on
startup and registers/unregisters each connected socket. `emit()` is safe to
call from any thread — it hops onto the loop via `run_coroutine_threadsafe`.

Kept dependency-free (sockets are duck-typed) to avoid importing `app` here,
which would be circular (`app` imports `pipeline`, `pipeline` imports this).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_loop: asyncio.AbstractEventLoop | None = None
_clients: set[Any] = set()  # Starlette WebSocket objects


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    global _loop
    _loop = loop


def unbind_loop() -> None:
    global _loop
    _loop = None
    _clients.clear()


def add_client(ws: Any) -> None:
    _clients.add(ws)


def remove_client(ws: Any) -> None:
    _clients.discard(ws)


def client_count() -> int:
    return len(_clients)


async def _broadcast(payload: dict) -> None:
    for ws in list(_clients):
        try:
            await ws.send_json(payload)
        except Exception:  # noqa: BLE001 - a dead socket shouldn't break the rest
            _clients.discard(ws)


def emit(event: str, **fields: Any) -> None:
    """Fire-and-forget a `{"event": ..., **fields}` message to every WS client."""
    if _loop is None or not _clients:
        return
    payload = {"event": event, **fields}
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(payload), _loop)
    except Exception:  # noqa: BLE001
        logger.debug("progress.emit(%s) failed to schedule", event, exc_info=True)
