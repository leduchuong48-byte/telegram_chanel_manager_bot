"""Log management and broadcasting core."""

from __future__ import annotations

import asyncio
import logging
from typing import List

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_LOG_QUEUE: asyncio.Queue[str] = asyncio.Queue(maxsize=1000)
_LOG_LOOP: asyncio.AbstractEventLoop | None = None


def set_log_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Bind the event loop used by the log broadcaster."""
    global _LOG_LOOP
    _LOG_LOOP = loop


class LogConnectionManager:
    """Manage WebSocket connections for log streaming."""

    def __init__(self) -> None:
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        """Send message to all connected clients."""
        if not self.active_connections:
            return

        connections = list(self.active_connections)
        results = await asyncio.gather(
            *(conn.send_text(message) for conn in connections),
            return_exceptions=True,
        )
        for conn, result in zip(connections, results):
            if isinstance(result, Exception):
                self.disconnect(conn)


class WebSocketLogHandler(logging.Handler):
    """Custom logging handler that pushes logs to an asyncio queue."""

    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    def emit(self, record: logging.LogRecord) -> None:
        """Push formatted log record to queue."""
        try:
            msg = self.format(record)
            if _LOG_LOOP is None:
                return
            _LOG_LOOP.call_soon_threadsafe(_enqueue_log, msg)
        except Exception:
            self.handleError(record)


def _enqueue_log(message: str) -> None:
    try:
        _LOG_QUEUE.put_nowait(message)
    except asyncio.QueueFull:
        return


async def log_broadcaster(manager: LogConnectionManager) -> None:
    """Background task to consume queue and broadcast logs."""
    while True:
        try:
            message = await _LOG_QUEUE.get()
            await manager.broadcast(message)
            _LOG_QUEUE.task_done()
        except Exception as exc:  # noqa: BLE001
            logger.error("日志广播任务异常: %s", exc, exc_info=True)
            await asyncio.sleep(1)


log_manager = LogConnectionManager()
