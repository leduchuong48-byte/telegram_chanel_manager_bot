"""Authentication API routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.config_manager import ConfigManager
from app.core.dependencies import get_current_user
from app.core.models import LoginRequest, TokenResponse, UserResponse
from app.core.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
token_router = APIRouter(prefix="/api", tags=["auth"])


# Global config manager instance
_config_manager: ConfigManager | None = None


def set_config_manager(manager: ConfigManager) -> None:
    """Set the global config manager instance."""
    global _config_manager
    _config_manager = manager


def _require_config_manager() -> ConfigManager:
    """Ensure config manager is initialized."""
    if _config_manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Config manager not initialized",
        )
    return _config_manager


@token_router.post("/token", response_model=TokenResponse)
async def issue_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
) -> TokenResponse:
    """
    OAuth2 password flow token endpoint.
    
    Returns JWT access token on success.
    """
    config_manager = _require_config_manager()

    user = config_manager.find_user(form_data.username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    password_hash = user.get("password_hash", "")
    if not password_hash or not verify_password(form_data.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(data={"sub": form_data.username})
    return TokenResponse(access_token=access_token)


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest) -> TokenResponse:
    """
    User login endpoint.
    
    Returns JWT access token on success.
    """
    config_manager = _require_config_manager()

    # Find user
    user = config_manager.find_user(request.username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify password
    password_hash = user.get("password_hash", "")
    if not password_hash or not verify_password(request.password, password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create token
    access_token = create_access_token(data={"sub": request.username})
    return TokenResponse(access_token=access_token)


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: Annotated[str, Depends(get_current_user)],
) -> UserResponse:
    """Get current authenticated user information."""
    return UserResponse(username=current_user)
