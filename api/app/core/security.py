"""Hash de senha (argon2) e tokens JWT (access + refresh com rotação)."""
import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from app.core.config import settings

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    return _ph.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        _ph.verify(password_hash, password)
        return True
    except VerifyMismatchError:
        return False
    except Exception:
        return False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(sub: uuid.UUID, role: str, partner_id: uuid.UUID | None) -> str:
    now = _now()
    payload = {
        "sub": str(sub),
        "role": role,
        "partner_id": str(partner_id) if partner_id else None,
        "type": "access",
        "jti": uuid.uuid4().hex,  # torna cada access único (mesmo emitido no mesmo segundo)
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_access_expiry)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(sub: uuid.UUID) -> tuple[str, datetime]:
    now = _now()
    expires = now + timedelta(seconds=settings.jwt_refresh_expiry)
    payload = {
        "sub": str(sub),
        "type": "refresh",
        "jti": uuid.uuid4().hex,
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def hash_token(token: str) -> str:
    """sha256 do refresh token — só o hash é guardado no banco."""
    return hashlib.sha256(token.encode()).hexdigest()
