"""Web admin users management API."""

from __future__ import annotations

import copy
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import MessageResponse
from app.core.security import get_password_hash

router = APIRouter(prefix="/api/users", tags=["users"])

_config_manager: ConfigManager | None = None


class UserItem(BaseModel):
    username: str


class UsersResponse(BaseModel):
    data: list[UserItem]


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=1)
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


def _get_users(config: dict) -> list[dict]:
    users = config.get("web_users", [])
    if isinstance(users, list):
        return [item for item in users if isinstance(item, dict)]
    return []


@router.get("", response_model=UsersResponse)
async def list_users(
    _: Annotated[str, Depends(get_current_user)],
) -> UsersResponse:
    """List web admin users (masked)."""
    config_manager = _get_config_manager()
    users = _get_users(config_manager.get_config())
    return UsersResponse(data=[UserItem(username=user.get("username", "")) for user in users if user.get("username")])


@router.post("", response_model=MessageResponse)
async def create_user(
    payload: UserCreateRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Create a new web admin user."""
    username = payload.username.strip()
    if not username:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名不能为空")
    if not payload.password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="密码不能为空")

    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())
    users = _get_users(new_config)

    if any(user.get("username") == username for user in users):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")

    users.append(
        {
            "username": username,
            "password_hash": get_password_hash(payload.password),
        }
    )
    new_config["web_users"] = users

    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reload_message)

    return MessageResponse(success=True, message="管理员已添加")


@router.delete("/{username}", response_model=MessageResponse)
async def delete_user(
    username: str,
    current_user: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Delete a web admin user."""
    target = username.strip()
    if not target:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="用户名不能为空")
    if target == current_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="不能删除当前登录账号")

    config_manager = _get_config_manager()
    new_config = copy.deepcopy(config_manager.get_config())
    users = _get_users(new_config)

    filtered = [user for user in users if user.get("username") != target]
    if len(filtered) == len(users):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    new_config["web_users"] = filtered
    success, message = config_manager.update_config(new_config)
    if not success:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message)

    reload_ok, reload_message = await config_manager.reload_config()
    if not reload_ok:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=reload_message)

    return MessageResponse(success=True, message="管理员已删除")
