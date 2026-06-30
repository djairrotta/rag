"""Dependencies: sessão, usuário atual, papéis e escopo multi-tenant."""
import uuid

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.errors import AppError, Codes
from app.core.security import decode_token
from app.db.session import SessionLocal
from app.models import User


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise AppError(Codes.UNAUTHORIZED, "token ausente", 401)
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
    except Exception:
        raise AppError(Codes.UNAUTHORIZED, "token inválido ou expirado", 401)
    if payload.get("type") != "access":
        raise AppError(Codes.UNAUTHORIZED, "tipo de token inválido", 401)
    try:
        user = db.get(User, uuid.UUID(payload["sub"]))
    except Exception:
        user = None
    if user is None:
        raise AppError(Codes.UNAUTHORIZED, "usuário não encontrado", 401)
    return user


def get_optional_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User | None:
    """Como get_current_user, mas devolve None em vez de 401 (fluxo grátis anônimo)."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        payload = decode_token(token)
        if payload.get("type") != "access":
            return None
        return db.get(User, uuid.UUID(payload["sub"]))
    except Exception:
        return None


def require_roles(*roles: str):
    """Exige que o papel do usuário esteja entre os permitidos."""
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise AppError(Codes.NOT_OWNER, "acesso negado para este papel", 403)
        return user
    return dep


def scope_filter(query, model, user: User):
    """Aplica o escopo multi-tenant (sem RLS): admin=tudo, partner=partner_id, user=user_id."""
    if user.role == "admin":
        return query
    if user.role == "partner":
        return query.where(model.partner_id == user.partner_id)
    return query.where(model.user_id == user.id)


def assert_owner(obj, user: User) -> None:
    """Garante posse do objeto; senão 403 NOT_OWNER. Admin sempre passa."""
    if user.role == "admin":
        return
    owner_id = getattr(obj, "partner_id", None) if user.role == "partner" else getattr(obj, "user_id", None)
    expected = user.partner_id if user.role == "partner" else user.id
    if owner_id != expected:
        raise AppError(Codes.NOT_OWNER, "acesso cruzado não permitido", 403)
