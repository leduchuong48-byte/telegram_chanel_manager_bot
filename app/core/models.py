"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field


class TokenResponse(BaseModel):
    """JWT token response."""
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    """User login request."""
    username: str
    password: str


class UserResponse(BaseModel):
    """Current user response."""
    username: str


class ConfigResponse(BaseModel):
    """Configuration response."""
    data: dict


class UpdateConfigRequest(BaseModel):
    """Configuration update request."""
    data: dict


class MessageResponse(BaseModel):
    """Generic message response."""
    success: bool
    message: str
