"""Password hashing, JWT issuing/verification and RBAC dependencies."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import get_db
from app.models.enums import UserRole
from app.models.identity import User

bearer_scheme = HTTPBearer(auto_error=False)


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------
# Recovery codes (confidential sessions) — stored only as a hash
# --------------------------------------------------------------------------
def generate_recovery_code() -> str:
    """Human-transcribable code, e.g. 'SUWA-7F3K-92QD'."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no ambiguous chars
    part1 = "".join(secrets.choice(alphabet) for _ in range(4))
    part2 = "".join(secrets.choice(alphabet) for _ in range(4))
    return f"SUWA-{part1}-{part2}"


def hash_recovery_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------
def create_access_token(user: User, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": user.id,
        "email": user.email,
        "role": str(user.role),
        "hospital_id": user.hospital_id,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_ttl_minutes),
        "type": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user.id,
        "iat": now,
        "exp": now + timedelta(days=settings.refresh_token_ttl_days),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired"
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from exc


# --------------------------------------------------------------------------
# Dependencies
# --------------------------------------------------------------------------
def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type"
        )

    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive"
        )
    return user


def require_roles(*roles: UserRole):
    """Dependency factory enforcing that the caller holds one of `roles`."""
    allowed = {str(r) for r in roles}

    def _dependency(current_user: User = Depends(get_current_user)) -> User:
        if str(current_user.role) not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This action requires one of: {', '.join(sorted(allowed))}. "
                    f"Your role is '{current_user.role}'."
                ),
            )
        return current_user

    return _dependency


# Convenience dependencies for the five roles.
require_patient = require_roles(UserRole.PATIENT)
require_guardian = require_roles(UserRole.GUARDIAN)
require_doctor = require_roles(UserRole.DOCTOR)
require_hospital_admin = require_roles(UserRole.HOSPITAL_ADMIN)
require_system_admin = require_roles(UserRole.SYSTEM_ADMIN)
require_clinical = require_roles(UserRole.DOCTOR, UserRole.SYSTEM_ADMIN)
require_admin = require_roles(UserRole.HOSPITAL_ADMIN, UserRole.SYSTEM_ADMIN)
