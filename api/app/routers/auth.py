"""Auth & sessões (api-contracts §1) — argon2 + JWT access/refresh com rotação."""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, get_db
from app.core.errors import AppError, Codes
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.models import RefreshToken, User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class TokenIn(BaseModel):
    refresh_token: str


def _user_public(user: User) -> dict:
    return {"id": str(user.id), "email": user.email, "name": user.name, "role": user.role}


def _issue_tokens(db: Session, user: User) -> tuple[str, str]:
    access = create_access_token(user.id, user.role, user.partner_id)
    refresh, expires = create_refresh_token(user.id)
    db.add(RefreshToken(user_id=user.id, token_hash=hash_token(refresh), expires_at=expires, revoked=False))
    db.commit()
    return access, refresh


@router.post("/register", status_code=201)
def register(body: RegisterIn, db: Session = Depends(get_db)) -> dict:
    if db.scalar(select(User).where(User.email == body.email)):
        raise AppError(Codes.EMAIL_TAKEN, "e-mail já cadastrado", 409)
    user = User(email=body.email, password_hash=hash_password(body.password), name=body.name, role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    access, refresh = _issue_tokens(db, user)
    return {"user": _user_public(user), "access_token": access, "refresh_token": refresh}


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.email == body.email))
    if user is None or not verify_password(user.password_hash, body.password):
        raise AppError(Codes.INVALID_CREDENTIALS, "credenciais inválidas", 401)
    access, refresh = _issue_tokens(db, user)
    return {"user": _user_public(user), "access_token": access, "refresh_token": refresh}


@router.post("/refresh")
def refresh(body: TokenIn, db: Session = Depends(get_db)) -> dict:
    from app.core.security import decode_token

    try:
        payload = decode_token(body.refresh_token)
    except Exception:
        raise AppError(Codes.INVALID_TOKEN, "refresh inválido ou expirado", 401)
    if payload.get("type") != "refresh":
        raise AppError(Codes.INVALID_TOKEN, "tipo de token inválido", 401)

    th = hash_token(body.refresh_token)
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == th))
    now = datetime.now(timezone.utc)
    if rt is None or rt.revoked or rt.expires_at <= now:
        raise AppError(Codes.INVALID_TOKEN, "refresh revogado ou expirado", 401)

    rt.revoked = True  # rotação: invalida o refresh usado
    user = db.get(User, uuid.UUID(payload["sub"]))
    if user is None:
        db.commit()
        raise AppError(Codes.UNAUTHORIZED, "usuário não encontrado", 401)
    access, new_refresh = _issue_tokens(db, user)  # também faz commit
    return {"access_token": access, "refresh_token": new_refresh}


@router.get("/me")
def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "partner_id": str(user.partner_id) if user.partner_id else None,
    }


@router.post("/logout", status_code=204)
def logout(body: TokenIn, db: Session = Depends(get_db)) -> Response:
    rt = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(body.refresh_token)))
    if rt is not None and not rt.revoked:
        rt.revoked = True
        db.commit()
    return Response(status_code=204)
