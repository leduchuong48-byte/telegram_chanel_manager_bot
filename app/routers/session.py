"""Telegram session login API."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    PasswordHashInvalidError,
    SessionPasswordNeededError,
)

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/session", tags=["session"])

_config_manager: ConfigManager | None = None

SESSION_TTL_SECONDS = 10 * 60


@dataclass
class PendingSession:
    client: TelegramClient
    phone_code_hash: str
    created_at: float
    needs_password: bool = False


_PENDING_SESSIONS: dict[str, PendingSession] = {}
_PENDING_LOCK = asyncio.Lock()


class SendCodeRequest(BaseModel):
    phone: str = Field(..., min_length=5)


class LoginRequest(BaseModel):
    phone: str = Field(..., min_length=5)
    code: str = Field(..., min_length=2)
    phone_code_hash: str = Field(..., min_length=5)


class TwoFARequest(BaseModel):
    phone: str = Field(..., min_length=5)
    password: str = Field(..., min_length=1)


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def _get_config_manager() -> ConfigManager:
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


def _normalize_phone(phone: str) -> str:
    return phone.strip()


def _resolve_session_path(config: dict[str, Any]) -> Path:
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}
    raw = str(bot_config.get("tg_session") or "").strip()
    if not raw:
        raw = os.getenv("TG_SESSION", "").strip() or "./sessions/user"
    path = Path(raw).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return path


def _session_file_candidates(session_path: Path) -> list[Path]:
    if session_path.suffix:
        return [session_path]
    return [session_path.with_suffix(".session"), session_path]


def _get_api_credentials(config: dict[str, Any]) -> tuple[int, str]:
    bot_config = config.get("bot", {}) if isinstance(config, dict) else {}
    if not isinstance(bot_config, dict):
        bot_config = {}

    api_id_raw = bot_config.get("api_id") or os.getenv("TG_API_ID", "")
    api_hash = str(bot_config.get("api_hash") or os.getenv("TG_API_HASH", "")).strip()

    api_id: int | None = None
    if isinstance(api_id_raw, int):
        api_id = api_id_raw
    elif isinstance(api_id_raw, str) and api_id_raw.strip():
        try:
            api_id = int(api_id_raw.strip())
        except ValueError:
            api_id = None

    if api_id is None or not api_hash:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="缺少 api_id 或 api_hash，请先在 Bot 设置中完善",
        )
    return api_id, api_hash


async def _cleanup_expired(now: float) -> None:
    async with _PENDING_LOCK:
        expired: list[str] = []
        for phone, pending in _PENDING_SESSIONS.items():
            if now - pending.created_at > SESSION_TTL_SECONDS:
                expired.append(phone)
        for phone in expired:
            pending = _PENDING_SESSIONS.pop(phone, None)
            if pending is not None:
                try:
                    await pending.client.disconnect()
                except Exception:
                    pass


def _build_client(config: dict[str, Any]) -> TelegramClient:
    api_id, api_hash = _get_api_credentials(config)
    session_path = _resolve_session_path(config)
    return TelegramClient(str(session_path), api_id, api_hash)


@router.get("/status")
async def session_status(
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Check current session status."""
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    session_path = _resolve_session_path(config)
    if not any(candidate.exists() for candidate in _session_file_candidates(session_path)):
        return {"connected": False}

    client = _build_client(config)
    try:
        await client.connect()
        authorized = await client.is_user_authorized()
        if not authorized:
            return {"connected": False}
        me = await client.get_me()
        return {
            "connected": True,
            "user_id": getattr(me, "id", None),
            "username": getattr(me, "username", None),
            "phone": getattr(me, "phone", None),
        }
    except Exception as exc:
        logger.warning("session_status_failed error=%s", exc)
        return {"connected": False}
    finally:
        await client.disconnect()


@router.post("/send_code")
async def send_code(
    payload: SendCodeRequest,
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Send login code to phone."""
    phone = _normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号不能为空")

    await _cleanup_expired(time.time())
    config_manager = _get_config_manager()
    config = config_manager.get_config()

    async with _PENDING_LOCK:
        existing = _PENDING_SESSIONS.pop(phone, None)
    if existing is not None:
        try:
            await existing.client.disconnect()
        except Exception:
            pass

    client = _build_client(config)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        pending = PendingSession(
            client=client,
            phone_code_hash=sent.phone_code_hash,
            created_at=time.time(),
        )
        async with _PENDING_LOCK:
            _PENDING_SESSIONS[phone] = pending
        return {"success": True, "phone_code_hash": sent.phone_code_hash, "message": "验证码已发送"}
    except PhoneNumberInvalidError:
        await client.disconnect()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号无效")
    except Exception as exc:
        await client.disconnect()
        logger.error("send_code_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="发送验证码失败")


@router.post("/login")
async def login(
    payload: LoginRequest,
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Login with phone + code."""
    phone = _normalize_phone(payload.phone)
    code = payload.code.strip()
    if not phone or not code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号或验证码为空")

    await _cleanup_expired(time.time())
    async with _PENDING_LOCK:
        pending = _PENDING_SESSIONS.get(phone)

    if pending is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="请先发送验证码")
    if payload.phone_code_hash != pending.phone_code_hash:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码哈希不匹配")

    preserve_pending = False
    try:
        await pending.client.connect()
        await pending.client.sign_in(
            phone=phone,
            code=code,
            phone_code_hash=pending.phone_code_hash,
        )
        me = await pending.client.get_me()
        return {
            "success": True,
            "need_password": False,
            "message": "登录成功",
            "user_id": getattr(me, "id", None),
        }
    except SessionPasswordNeededError:
        pending.needs_password = True
        preserve_pending = True
        return {"success": True, "need_password": True, "message": "需要二次验证密码"}
    except PhoneCodeInvalidError:
        preserve_pending = True
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误")
    except PhoneCodeExpiredError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="验证码已过期，请重新发送")
    except Exception as exc:
        logger.error("login_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="登录失败")
    finally:
        if preserve_pending or (pending is not None and pending.needs_password):
            await pending.client.disconnect()
        else:
            async with _PENDING_LOCK:
                _PENDING_SESSIONS.pop(phone, None)
            await pending.client.disconnect()


@router.post("/2fa")
async def login_2fa(
    payload: TwoFARequest,
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Handle 2FA password login."""
    phone = _normalize_phone(payload.phone)
    if not phone:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="手机号不能为空")

    await _cleanup_expired(time.time())
    async with _PENDING_LOCK:
        pending = _PENDING_SESSIONS.get(phone)

    if pending is None or not pending.needs_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未检测到需要二次验证的会话")

    preserve_pending = False
    try:
        await pending.client.connect()
        await pending.client.sign_in(password=payload.password.strip())
        me = await pending.client.get_me()
        return {
            "success": True,
            "message": "二次验证成功",
            "user_id": getattr(me, "id", None),
        }
    except PasswordHashInvalidError:
        preserve_pending = True
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="二次验证密码错误")
    except Exception as exc:
        logger.error("login_2fa_failed error=%s", exc)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="二次验证失败")
    finally:
        if not preserve_pending:
            async with _PENDING_LOCK:
                _PENDING_SESSIONS.pop(phone, None)
        await pending.client.disconnect()


@router.post("/logout")
async def logout(
    _: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove session file and clear pending states."""
    config_manager = _get_config_manager()
    config = config_manager.get_config()
    session_path = _resolve_session_path(config)

    async with _PENDING_LOCK:
        pending_items = list(_PENDING_SESSIONS.items())
        _PENDING_SESSIONS.clear()

    for _, pending in pending_items:
        try:
            await pending.client.disconnect()
        except Exception:
            pass

    for candidate in _session_file_candidates(session_path):
        if candidate.exists():
            try:
                candidate.unlink()
            except OSError:
                logger.warning("session_file_remove_failed path=%s", candidate)

    return {"success": True, "message": "已清理会话文件"}
