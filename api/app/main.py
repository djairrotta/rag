"""Ponto de entrada da API do SEGURA MULTAS.

M2: auth (argon2 + JWT access/refresh), roles, escopo multi-tenant, claim,
healthchecks. Migrações Alembic em /api/alembic. Demais domínios (análise real,
pagamento, geração, painéis) entram nas missões seguintes.
"""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.core.errors import AppError, app_error_handler
from app.routers import analyses, payments, recursos, auth, health, cases

app = FastAPI(
    title="SEGURA MULTAS · API",
    version="0.2.0",
    description="Backend self-hosted — análise de multas, veredito e geração de recursos.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(AppError, app_error_handler)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(cases.router)
app.include_router(analyses.router)
app.include_router(payments.router)
app.include_router(recursos.router)


@app.get("/", tags=["meta"])
def root() -> dict:
    return {"service": "seguramultas-api", "status": "ok", "version": app.version}


@app.get("/health/db", tags=["health"])
def health_db(db: Session = Depends(get_db)) -> dict:
    db.execute(text("SELECT 1"))
    return {"status": "healthy", "db": "ok"}
