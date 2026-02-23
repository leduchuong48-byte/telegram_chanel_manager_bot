"""Security utilities for authentication and password hashing."""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

# Password hashing configuration
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT configuration
SECRET_KEY = "your-secret-key-change-in-production"  # Will be overridden from env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours


def set_secret_key(key: str) -> None:
    """Set the secret key for JWT encryption."""
    global SECRET_KEY
    SECRET_KEY = key


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a plain password using bcrypt."""
    return pwd_context.hash(password)


def create_access_token(
    data: dict[str, Any], expires_delta: timedelta | None = None
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and validate a JWT token."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None


def get_current_user_from_token(token: str) -> str | None:
    """Validate token and return username."""
    payload = decode_token(token)
    if payload is None:
        return None
    username: str | None = payload.get("sub")
    if not username:
        return None
    return username
