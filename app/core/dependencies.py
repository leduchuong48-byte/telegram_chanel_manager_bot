"""FastAPI dependencies for authentication."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> str:
    """
    Get current authenticated user from JWT token.
    
    Raises:
        HTTPException: If token is invalid or missing
    """
    payload = decode_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    username: str | None = payload.get("sub")
    if username is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token claims",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return username
