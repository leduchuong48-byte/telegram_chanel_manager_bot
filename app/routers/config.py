"""Configuration management API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import ConfigResponse, MessageResponse, UpdateConfigRequest

router = APIRouter(prefix="/api/config", tags=["config"])

# Global config manager instance
_config_manager: ConfigManager | None = None


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def _get_config_manager() -> ConfigManager:
    """Get the config manager instance."""
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


@router.get("", response_model=ConfigResponse)
async def get_config(
    _: Annotated[str, Depends(get_current_user)],
) -> ConfigResponse:
    """Get current configuration (requires authentication)."""
    config_manager = _get_config_manager()
    return ConfigResponse(data=config_manager.get_config())


@router.put("", response_model=MessageResponse)
async def update_config(
    request: UpdateConfigRequest,
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Update configuration and create backup (requires authentication)."""
    config_manager = _get_config_manager()
    success, message = config_manager.update_config(request.data)
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    
    return MessageResponse(success=True, message=message)


@router.post("/reload", response_model=MessageResponse)
async def reload_config(
    _: Annotated[str, Depends(get_current_user)],
) -> MessageResponse:
    """Reload configuration from disk (hot reload)."""
    config_manager = _get_config_manager()
    success, message = await config_manager.reload_config()
    
    if not success:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )
    
    return MessageResponse(success=True, message=message)
