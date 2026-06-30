"""Pagamentos (M5) — porte de create-payment + asaas-webhook.

POST /payments        cria a cobrança (preço SEMPRE server-side via pricing).
GET  /payments/{id}   status (escopo do dono) — usado pelo polling do frontend.
POST /webhooks/asaas  recebe eventos do Asaas (valida asaas-access-token).
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Header
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_owner, get_current_user, get_db
from app.core.errors import AppError, Codes
from app.core.pricing import decide_price
from app.models import Analysis, Payment, User
from app.services import asaas

router = APIRouter(tags=["payments"])


class CustomerIn(BaseModel):
    name: str | None = None
    cpf_cnpj: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    postal_code: str | None = None


class PaymentIn(BaseModel):
    analysis_id: uuid.UUID
    billing_type: str = "UNDEFINED"            # UNDEFINED|PIX|CREDIT_CARD|BOLETO
    customer: CustomerIn | None = None


@router.post("/payments")
def create_payment(body: PaymentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    analysis = db.get(Analysis, body.analysis_id)
    if analysis is None:
        raise AppError(Codes.NOT_FOUND, "análise não encontrada", 404)
    # só o dono paga (admin passa)
    if analysis.user_id is not None:
        assert_owner(analysis, user)

    decision = decide_price(analysis.campos)
    if decision.kind == "CONTACT_REQUIRED":
        analysis.contact_required = True
        db.commit()
        return JSONResponse(status_code=200, content={
            "success": False, "error_code": "CONTACT_REQUIRED",
            "reason": decision.reason, "message": decision.message,
        })

    cust = body.customer or CustomerIn()
    customer_id = asaas.find_or_create_customer(
        email=user.email, name=cust.name or user.name, cpf_cnpj=cust.cpf_cnpj,
        phone=cust.phone, address=cust.address, city=cust.city, postal_code=cust.postal_code,
    )
    due = (date.today() + timedelta(days=3)).isoformat()
    fine = decision.fine_value
    desc = "SEGURA MULTAS - Recurso administrativo" + (f" (multa R$ {fine:.2f})" if fine else "")
    pay = asaas.create_payment(
        customer_id=customer_id, value=decision.amount, due_date=due, description=desc,
        external_reference=f"{user.id}_{body.analysis_id}", billing_type=body.billing_type,
    )

    row = Payment(
        user_id=user.id, analysis_id=analysis.id, asaas_id=pay["id"], asaas_customer_id=customer_id,
        method=pay.get("billingType", body.billing_type), amount_brl=Decimal(str(pay.get("value", decision.amount))),
        status=pay.get("status", "PENDING"), due_date=pay.get("dueDate", due),
    )
    db.add(row); db.commit()

    return {
        "success": True,
        "payment_id": pay["id"],
        "invoice_url": pay.get("invoiceUrl"),
        "bank_slip_url": pay.get("bankSlipUrl"),
        "pix_code": pay.get("pixQrCodeUrl"),
        "value": pay.get("value", decision.amount),
        "due_date": pay.get("dueDate", due),
        "status": pay.get("status", "PENDING"),
        "price_source": decision.source,
        "fine_value": fine,
        "engine": asaas.provider_label(),
    }


@router.get("/payments/{payment_id}")
def payment_status(payment_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    p = db.get(Payment, payment_id)
    if p is None:
        raise AppError(Codes.NOT_FOUND, "pagamento não encontrado", 404)
    assert_owner(p, user)
    return {
        "payment_id": str(p.id), "asaas_payment_id": p.asaas_id, "status": p.status,
        "amount": float(p.amount_brl), "method": p.method, "due_date": p.due_date,
        "paid_at": p.paid_at.isoformat() if p.paid_at else None,
        "analysis_id": str(p.analysis_id),
    }


# ----------------------------------------------------------------- webhook
class WebhookIn(BaseModel):
    event: str | None = None
    payment: dict | None = None


_PAID = {"PAYMENT_CONFIRMED", "PAYMENT_RECEIVED"}
_CANCELLED = {"PAYMENT_DELETED", "PAYMENT_REFUNDED"}


@router.post("/webhooks/asaas")
def asaas_webhook(
    body: WebhookIn,
    asaas_access_token: str | None = Header(default=None, alias="asaas-access-token"),
    db: Session = Depends(get_db),
):
    # valida o token do webhook (se configurado)
    if settings.asaas_webhook_token and asaas_access_token != settings.asaas_webhook_token:
        return JSONResponse(status_code=401, content={"error": "Unauthorized"})

    if not body.event or not body.payment:
        return JSONResponse(status_code=400, content={"error": "Invalid webhook payload"})

    asaas_pid = body.payment.get("id")
    billing = body.payment.get("billingType")
    p = db.query(Payment).filter(Payment.asaas_id == asaas_pid).first()
    if p is None:
        # tolerante: pagamento ainda não está no nosso sistema
        return {"success": True, "message": "Payment not found in system"}

    event = body.event
    if event in _PAID:
        if p.status != "CONFIRMED":
            p.status = "CONFIRMED"
            p.paid_at = datetime.now(timezone.utc)
            if billing:
                p.method = billing
            an = db.get(Analysis, p.analysis_id)
            if an is not None:
                an.resource_available = True   # libera o recurso (M7 gera o documento)
            db.commit()
    elif event == "PAYMENT_OVERDUE":
        p.status = "OVERDUE"; db.commit()
    elif event in _CANCELLED:
        p.status = "DELETED" if event == "PAYMENT_DELETED" else "REFUNDED"
        an = db.get(Analysis, p.analysis_id)
        if an is not None:
            an.resource_available = False
        db.commit()
    # outros eventos: ignora

    return {"success": True}
