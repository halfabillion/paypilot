"""Offline PayPal stand-in. Every id and link is labeled as a fixture."""

from __future__ import annotations

import secrets
from datetime import date
from decimal import Decimal
from urllib.parse import quote

from paypilot.db import Database
from paypilot.errors import PayPalAPIError
from paypilot.models import (
    SEEDED_CAPTURE_AMOUNT,
    SEEDED_CAPTURE_CURRENCY,
    SEEDED_CAPTURE_ID,
    SEEDED_INVOICE_ID,
    CallTrace,
    CreateInvoiceRequest,
    CreatePaymentLinkRequest,
    Invoicer,
    MOCK_LINK_HOST,
    RecordRefundRequest,
    RefundCaptureRequest,
)
from paypilot.money import display_money, format_money
from paypilot.paypal.payloads import (
    capture_refund_body,
    invoice_create_body,
    invoice_send_body,
    payment_link_body,
    record_refund_body,
)
from paypilot.status import plain_status

_MOCK_LABEL = "MOCK FIXTURE — not a PayPal sandbox response."
_PAID_STATUSES = {"PAID", "MARKED_AS_PAID", "PARTIALLY_REFUNDED"}


class MockPayPal:
    """Session-scoped fixtures with the same method names as the sandbox client."""

    mode = "mock"

    def __init__(self, db: Database, invoicer: Invoicer):
        self.db = db
        self.invoicer = invoicer
        self.calls: list[str] = []

    def seed(self, session_id: str) -> None:
        if self.db.get_mock_invoice(session_id, SEEDED_INVOICE_ID):
            return
        self.db.save_mock_invoice(
            session_id,
            SEEDED_INVOICE_ID,
            {
                "id": SEEDED_INVOICE_ID,
                "status": "PAID",
                "recipient_email": "seeded-buyer@example.com",
                "recipient_name": "Seeded Buyer",
                "currency_code": "GBP",
                "items": [
                    {"name": "Seeded paid work", "quantity": "1", "unit_amount": "40.00"}
                ],
                "total": "40.00",
                "refunded_total": "0.00",
                "hosted_url": f"https://{MOCK_LINK_HOST}/invoice/p/#{SEEDED_INVOICE_ID}",
                "seeded": True,
                "note": (
                    "Seeded mock invoice already marked PAID. "
                    "Not a PayPal sandbox payment and not evidence of a capture."
                ),
                "invoice_number": "SEEDED-MOCK-PAID",
                "mock": True,
            },
        )

    def create_draft_invoice(
        self, request: CreateInvoiceRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("create_draft_invoice")
        cached = self.db.get_idempotent(request_id)
        if cached:
            return cached, []
        invoice_id = _mock_id("INV2-MOCK-")
        total = format_money(sum((item.quantity * item.unit_amount for item in request.items), Decimal("0")))
        hosted = f"https://{MOCK_LINK_HOST}/invoice/p/#{invoice_id}"
        invoice = {
            "id": invoice_id,
            "status": "DRAFT",
            "recipient_email": request.recipient_email,
            "recipient_name": request.recipient_name,
            "currency_code": request.currency_code,
            "items": [
                {
                    "name": item.name,
                    "quantity": str(int(item.quantity)),
                    "unit_amount": format_money(item.unit_amount),
                }
                for item in request.items
            ],
            "total": total,
            "refunded_total": "0.00",
            "hosted_url": hosted,
            "seeded": False,
            "note": request.note,
            "invoice_number": request.invoice_number,
            "mock": True,
        }
        self.db.save_mock_invoice(session_id, invoice_id, invoice)
        self.db.put_idempotent(request_id, _public_invoice(invoice))
        body = invoice_create_body(
            request,
            self.invoicer,
            invoice_date=date.today().isoformat(),
            fallback_email="merchant@example.com",
        )
        trace = _trace(
            "POST",
            "/v2/invoicing/invoices",
            201,
            invoice_id,
            request_body=body,
            response_body={
                "id": invoice_id,
                "status": "DRAFT",
                "mock": True,
                "label": _MOCK_LABEL,
                "due_amount": {"currency_code": request.currency_code, "value": total},
            },
            summary=f"POST /v2/invoicing/invoices → 201 {invoice_id} DRAFT",
        )
        return _public_invoice(invoice), [trace]

    def send_invoice(
        self, invoice_id: str, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("send_invoice")
        cached = self.db.get_idempotent(request_id)
        if cached:
            return cached, []
        invoice = self._require_invoice(session_id, invoice_id)
        path = f"/v2/invoicing/invoices/{quote(invoice_id, safe='')}/send"
        if invoice["status"] != "DRAFT":
            raise _failure(
                422,
                path,
                invoice_id,
                f"Only a DRAFT invoice can be sent. Current status is {invoice['status']}.",
                request_body=invoice_send_body(),
            )
        invoice["status"] = "SENT"
        self.db.save_mock_invoice(session_id, invoice_id, invoice)
        public = _public_invoice(invoice)
        self.db.put_idempotent(request_id, public)
        send_trace = _trace(
            "POST",
            path,
            202,
            invoice_id,
            request_body=invoice_send_body(),
            response_body={"mock": True, "label": _MOCK_LABEL},
            summary=f"POST {path} → 202 accepted",
        )
        get_trace = _trace(
            "GET",
            f"/v2/invoicing/invoices/{invoice_id}",
            200,
            invoice_id,
            response_body={
                "id": invoice_id,
                "status": "SENT",
                "mock": True,
                "label": _MOCK_LABEL,
                "detail": {"metadata": {"recipient_view_url": invoice["hosted_url"]}},
            },
            summary=f"GET /v2/invoicing/invoices/{invoice_id} → 200 SENT",
        )
        return public, [send_trace, get_trace]

    def get_invoice(self, invoice_id: str, *, session_id: str) -> tuple[dict, list[CallTrace]]:
        self.calls.append("get_invoice")
        invoice = self._require_invoice(session_id, invoice_id)
        path = f"/v2/invoicing/invoices/{quote(invoice_id, safe='')}"
        trace = _trace(
            "GET",
            path,
            200,
            invoice_id,
            response_body={
                "id": invoice_id,
                "status": invoice["status"],
                "mock": True,
                "label": _MOCK_LABEL,
                "due_amount": {
                    "currency_code": invoice["currency_code"],
                    "value": invoice["total"],
                },
            },
            summary=f"GET {path} → 200 {invoice['status']}",
        )
        return _public_invoice(invoice), [trace]

    def list_invoices(self, *, session_id: str) -> tuple[dict, list[CallTrace]]:
        self.calls.append("list_invoices")
        invoices = [_public_invoice(row) for row in self.db.list_mock_invoices(session_id)]
        trace = _trace(
            "GET",
            "/v2/invoicing/invoices?page=1&page_size=20&total_required=true",
            200,
            None,
            response_body={
                "items": [{"id": row["id"], "status": row["status"], "mock": True} for row in invoices],
                "mock": True,
                "label": _MOCK_LABEL,
            },
            summary=f"GET /v2/invoicing/invoices → 200 {len(invoices)} invoices",
        )
        return {"invoices": invoices}, [trace]

    def create_payment_link(
        self, request: CreatePaymentLinkRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("create_payment_link")
        cached = self.db.get_idempotent(request_id)
        if cached:
            return cached, []
        link_id = _mock_id("PLB-MOCK-")
        url = f"https://{MOCK_LINK_HOST}/ncp/payment/{link_id}"
        result = {
            "id": link_id,
            "status": "ACTIVE",
            "payment_link": url,
            "link_kind": "mock",
            "title": request.title,
            "currency_code": request.currency_code,
            "total": format_money(request.unit_amount),
            "total_display": display_money(request.currency_code, request.unit_amount),
            "reusable": request.reusable,
            "mock": True,
        }
        self.db.save_mock_link(session_id, link_id, result)
        self.db.put_idempotent(request_id, result)
        trace = _trace(
            "POST",
            "/v1/checkout/payment-resources",
            201,
            link_id,
            request_body=payment_link_body(request, return_url=""),
            response_body={
                "id": link_id,
                "status": "ACTIVE",
                "mock": True,
                "label": _MOCK_LABEL,
                "links": [{"rel": "payment_link", "href": url, "method": "GET"}],
            },
            summary=f"POST /v1/checkout/payment-resources → 201 {link_id}",
        )
        return result, [trace]

    def refund_capture(
        self, request: RefundCaptureRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("refund_capture")
        cached = self.db.get_idempotent(request_id)
        if cached:
            return cached, []
        path = f"/v2/payments/captures/{quote(request.capture_id, safe='')}/refund"
        body = capture_refund_body(request)
        if request.capture_id != SEEDED_CAPTURE_ID:
            raise _failure(
                404,
                path,
                request.capture_id,
                (
                    f"Unknown capture {request.capture_id}. "
                    f"Mock mode only has the seeded fixture {SEEDED_CAPTURE_ID}."
                ),
                request_body=body,
                moves_money=True,
            )
        if request.currency_code != SEEDED_CAPTURE_CURRENCY:
            raise _failure(
                422,
                path,
                request.capture_id,
                f"Capture {SEEDED_CAPTURE_ID} is {SEEDED_CAPTURE_CURRENCY}. PayPal was not called with a mismatched currency.",
                request_body=body,
                moves_money=True,
            )
        already = sum(
            (Decimal(amount) for amount in self.db.capture_refund_amounts(session_id, request.capture_id)),
            Decimal("0"),
        )
        if already + request.amount > SEEDED_CAPTURE_AMOUNT:
            raise _failure(
                422,
                path,
                request.capture_id,
                (
                    f"Refund exceeds the remaining {SEEDED_CAPTURE_CURRENCY} "
                    f"{format_money(SEEDED_CAPTURE_AMOUNT - already)} on {SEEDED_CAPTURE_ID}."
                ),
                request_body=body,
                moves_money=True,
            )
        refund_id = _mock_id("MOCK-REFUND-")
        result = {
            "id": refund_id,
            "status": "COMPLETED",
            "capture_id": request.capture_id,
            "currency_code": request.currency_code,
            "total": format_money(request.amount),
            "total_display": display_money(request.currency_code, request.amount),
            "moves_money": True,
            "mock": True,
            "note": (
                "Mock fixture refund. No PayPal sandbox refund was created, and no money moved at PayPal."
            ),
        }
        self.db.add_capture_refund(
            refund_id, session_id, request.capture_id, result["total"], result
        )
        self.db.put_idempotent(request_id, result)
        trace = _trace(
            "POST",
            path,
            201,
            refund_id,
            request_body=body,
            response_body={
                "id": refund_id,
                "status": "COMPLETED",
                "mock": True,
                "label": _MOCK_LABEL,
            },
            summary=f"POST {path} → 201 {refund_id} COMPLETED",
            moves_money=True,
        )
        return result, [trace]

    def record_invoice_refund(
        self, request: RecordRefundRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("record_invoice_refund")
        cached = self.db.get_idempotent(request_id)
        if cached:
            return cached, []
        path = f"/v2/invoicing/invoices/{quote(request.invoice_id, safe='')}/refunds"
        body = record_refund_body(request)
        invoice = self._require_invoice(session_id, request.invoice_id)
        if invoice["status"] not in _PAID_STATUSES:
            raise _failure(
                422,
                path,
                request.invoice_id,
                (
                    f"Invoice {request.invoice_id} is {invoice['status']}, not paid. "
                    "Recording an external refund does not transfer money, and this invoice "
                    "has no payment to record against."
                ),
                request_body=body,
            )
        if request.currency_code != invoice["currency_code"]:
            raise _failure(
                422,
                path,
                request.invoice_id,
                f"Invoice currency is {invoice['currency_code']}.",
                request_body=body,
            )
        remaining = Decimal(invoice["total"]) - Decimal(invoice["refunded_total"])
        if request.amount > remaining:
            raise _failure(
                422,
                path,
                request.invoice_id,
                f"Recorded refund exceeds the remaining {format_money(remaining)}.",
                request_body=body,
            )
        refunded = Decimal(invoice["refunded_total"]) + request.amount
        invoice["refunded_total"] = format_money(refunded)
        invoice["status"] = (
            "MARKED_AS_REFUNDED" if refunded == Decimal(invoice["total"]) else "PARTIALLY_REFUNDED"
        )
        self.db.save_mock_invoice(session_id, request.invoice_id, invoice)
        refund_id = _mock_id("EXTR-MOCK-")
        result = {
            "id": refund_id,
            "status": "RECORDED",
            "invoice_id": request.invoice_id,
            "invoice_status": invoice["status"],
            "currency_code": request.currency_code,
            "total": format_money(request.amount),
            "total_display": display_money(request.currency_code, request.amount),
            "method": request.method,
            "moves_money": False,
            "mock": True,
            "note": "Recorded on the invoice. Recording an external refund does not transfer money.",
        }
        self.db.put_idempotent(request_id, result)
        trace = _trace(
            "POST",
            path,
            200,
            refund_id,
            request_body=body,
            response_body={"refund_id": refund_id, "mock": True, "label": _MOCK_LABEL},
            summary=f"POST {path} → 200 {refund_id} recorded",
            moves_money=False,
        )
        return result, [trace]

    def _require_invoice(self, session_id: str, invoice_id: str) -> dict:
        invoice = self.db.get_mock_invoice(session_id, invoice_id)
        if invoice is None:
            path = f"/v2/invoicing/invoices/{quote(invoice_id, safe='')}"
            raise _failure(
                404,
                path,
                invoice_id,
                f"Invoice {invoice_id} was not found in this session.",
            )
        return invoice


def _mock_id(prefix: str) -> str:
    return prefix + secrets.token_hex(4).upper()


def _public_invoice(invoice: dict) -> dict:
    item_names = ", ".join(item["name"] for item in invoice["items"])
    return {
        "id": invoice["id"],
        "status": invoice["status"],
        "status_plain": plain_status(invoice["status"]),
        "recipient_email": invoice["recipient_email"],
        "recipient_name": invoice["recipient_name"],
        "currency_code": invoice["currency_code"],
        "total": invoice["total"],
        "total_display": display_money(invoice["currency_code"], invoice["total"]),
        "refunded_total": invoice["refunded_total"],
        "item_summary": item_names,
        "hosted_url": invoice["hosted_url"],
        "hosted_url_kind": "mock",
        "seeded": invoice["seeded"],
        "mock": True,
        "invoice_number": invoice["invoice_number"],
    }


def _trace(
    method: str,
    path: str,
    status_code: int,
    resource_id: str | None,
    *,
    request_body: dict | None = None,
    response_body: dict | None = None,
    summary: str,
    moves_money: bool = False,
) -> CallTrace:
    return CallTrace(
        method=method,
        path=path,
        status_code=status_code,
        summary=summary,
        request_body=request_body,
        response_body=response_body,
        resource_id=resource_id,
        moves_money=moves_money,
        mock=True,
        debug_id=None,
    )


def _failure(
    status_code: int,
    path: str,
    resource_id: str | None,
    message: str,
    *,
    request_body: dict | None = None,
    moves_money: bool = False,
) -> PayPalAPIError:
    trace = _trace(
        "POST" if request_body is not None else "GET",
        path,
        status_code,
        resource_id,
        request_body=request_body,
        response_body={"mock": True, "label": _MOCK_LABEL, "message": message},
        summary=f"{path} → {status_code}",
        moves_money=moves_money,
    )
    return PayPalAPIError(message, status_code=status_code, traces=[trace])
