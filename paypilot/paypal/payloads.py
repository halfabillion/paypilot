"""JSON bodies sent to PayPal. Shared by the sandbox client and the mock trace."""

from __future__ import annotations

from paypilot.models import (
    CreateInvoiceRequest,
    CreatePaymentLinkRequest,
    Invoicer,
    RecordRefundRequest,
    RefundCaptureRequest,
)
from paypilot.money import format_money


def _billing_info(full_name: str, email: str) -> dict:
    billing = {"email_address": email}
    if full_name and full_name.strip():
        billing["name"] = person_name(full_name)
    return billing


def person_name(full: str) -> dict[str, str]:
    parts = full.split()
    if len(parts) == 1:
        return {"given_name": parts[0], "surname": "Buyer"}
    return {"given_name": parts[0], "surname": " ".join(parts[1:])[:140]}


def invoicer_body(invoicer: Invoicer, *, fallback_email: str | None) -> dict:
    body: dict = {
        "name": {"given_name": invoicer.given_name, "surname": invoicer.surname},
        "business_name": invoicer.business_name,
    }
    email = invoicer.email or fallback_email
    if email:
        body["email_address"] = email
    return body


def invoice_create_body(
    request: CreateInvoiceRequest,
    invoicer: Invoicer,
    *,
    invoice_date: str,
    fallback_email: str | None,
) -> dict:
    term = dict(request.payment_term or {"term_type": "DUE_ON_RECEIPT"})
    if not term.get("due_date"):
        term.pop("due_date", None)
    detail: dict = {
        "invoice_number": request.invoice_number,
        "invoice_date": invoice_date,
        "currency_code": request.currency_code,
        "payment_term": {"term_type": term.get("term_type") or "DUE_ON_RECEIPT"},
    }
    if term.get("due_date"):
        detail["payment_term"]["due_date"] = term["due_date"]
    if request.note:
        detail["note"] = request.note
    return {
        "detail": detail,
        "invoicer": invoicer_body(invoicer, fallback_email=fallback_email),
        "primary_recipients": [
            {"billing_info": _billing_info(request.recipient_name, request.recipient_email)}
        ],
        "items": [
            {
                "name": item.name,
                "quantity": str(int(item.quantity)),
                "unit_amount": {
                    "currency_code": request.currency_code,
                    "value": format_money(item.unit_amount),
                },
                "unit_of_measure": "QUANTITY",
            }
            for item in request.items
        ],
    }


def invoice_send_body() -> dict:
    return {"send_to_recipient": True, "send_to_invoicer": False}


def payment_link_body(request: CreatePaymentLinkRequest, *, return_url: str) -> dict:
    body: dict = {
        "integration_mode": "LINK",
        "type": "BUY_NOW",
        "line_items": [
            {
                "name": request.title,
                "unit_amount": {
                    "currency_code": request.currency_code,
                    "value": format_money(request.unit_amount),
                },
            }
        ],
    }
    # "MULTIPLE" is the value documented by PayPal for a reusable link.
    # The public enum page did not list a single-use token on 7 Nov 2026,
    # so a one-time link omits `reusable` rather than sending an unverified value.
    if request.reusable:
        body["reusable"] = "MULTIPLE"
    if return_url:
        body["return_url"] = return_url
    return body


def capture_refund_body(request: RefundCaptureRequest) -> dict:
    body: dict = {
        "amount": {
            "currency_code": request.currency_code,
            "value": format_money(request.amount),
        }
    }
    if request.note:
        body["note_to_payer"] = request.note
    return body


def record_refund_body(request: RecordRefundRequest) -> dict:
    return {
        "method": request.method,
        "refund_date": request.refund_date,
        "amount": {
            "currency_code": request.currency_code,
            "value": format_money(request.amount),
        },
    }
