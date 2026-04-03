"""WebSocket router for system logs."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status

from app.core.log_manager import log_manager
from app.core.security import get_current_user_from_token

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/logs")
async def websocket_logs(
    websocket: WebSocket,
    token: str = Query(..., description="JWT Access Token"),
) -> None:
    """
    WebSocket endpoint for real-time logs.
    Usage: ws://host/ws/logs?token=<access_token>
    """
    user = get_current_user_from_token(token)
    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await log_manager.connect(websocket)

    try:
        await websocket.send_text("系统：已连接日志流。")
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        log_manager.disconnect(websocket)
    except Exception as exc:  # noqa: BLE001
        logger.error("WebSocket error: %s", exc, exc_info=True)
        log_manager.disconnect(websocket)
