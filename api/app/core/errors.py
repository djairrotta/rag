"""Envelope de erro padrão + códigos de negócio (api-contracts §0 e §13)."""
from fastapi import Request
from fastapi.responses import JSONResponse


class Codes:
    UNAUTHORIZED = "UNAUTHORIZED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION = "VALIDATION"
    EMAIL_TAKEN = "EMAIL_TAKEN"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    INVALID_TOKEN = "INVALID_TOKEN"
    # Negócio (§13)
    CONTACT_REQUIRED = "CONTACT_REQUIRED"
    PAYMENT_REQUIRED = "PAYMENT_REQUIRED"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    CLAIM_INVALID = "CLAIM_INVALID"
    NOT_OWNER = "NOT_OWNER"
    ANALYSIS_NOT_READY = "ANALYSIS_NOT_READY"


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )
