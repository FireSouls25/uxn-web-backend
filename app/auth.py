"""Password hashing + JWT access/refresh tokens.

Access tokens are short-lived and stateless. Refresh tokens rotate:
each use revokes the old row and issues a new pair, so a stolen
refresh token is usable at most once before the legitimate client's
next refresh invalidates the whole chain's sibling.
"""
from __future__ import annotations

import datetime
import hashlib
import uuid

import bcrypt
import jwt

from .config import settings

ALGORITHM = "HS256"


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode())
    except ValueError:
        return False


def refresh_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class TokenError(Exception):
    pass


def create_access_token(user_id: str) -> tuple[str, int]:
    expires_in = settings.access_minutes * 60
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": _now(),
        "exp": _now() + datetime.timedelta(seconds=expires_in),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), expires_in


def create_refresh_token(user_id: str) -> tuple[str, str, datetime.datetime]:
    jti = str(uuid.uuid4())
    expires_at = _now() + datetime.timedelta(days=settings.refresh_days)
    payload = {"sub": user_id, "type": "refresh", "jti": jti, "exp": expires_at}
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, jti, expires_at


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as e:
        raise TokenError(str(e))
    if payload.get("type") != expected_type or not payload.get("sub"):
        raise TokenError("wrong token type")
    return payload
