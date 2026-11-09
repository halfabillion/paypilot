"""Typed proposals. The language model fills fields; these types hold them."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class IntentDraft:
    action: str
    assistant_message: str
    missing: list[str] = field(default_factory=list)
    fields: dict[str, Any] = field(default_factory=dict)


@dataclass
class LineItem:
    name: str
    quantity: Decimal
    unit_amount: Decimal


@dataclass
class CreateInvoiceRequest:
    recipient_email: str
    recipient_name: str
    currency_code: str
    items: list[LineItem]
    note: str | None
    invoice_number: str
    payment_term: dict | None = None


@dataclass
class CreatePaymentLinkRequest:
    title: str
    currency_code: str
    unit_amount: Decimal
    reusable: bool


@dataclass
class RefundCaptureRequest:
    capture_id: str
    currency_code: str
    amount: Decimal
    note: str | None


@dataclass
class RecordRefundRequest:
    invoice_id: str
    currency_code: str
    amount: Decimal
    method: str
    refund_date: str


@dataclass(frozen=True)
class Invoicer:
    email: str
    given_name: str
    surname: str
    business_name: str


@dataclass
class CallTrace:
    method: str
    path: str
    status_code: int
    summary: str
    request_body: dict | None = None
    response_body: dict | None = None
    debug_id: str | None = None
    resource_id: str | None = None
    moves_money: bool = False
    mock: bool = False


READ_ACTIONS = {"get_invoice", "list_invoices"}
MUTATING_ACTIONS = {
    "create_invoice",
    "send_invoice",
    "create_payment_link",
    "refund_capture",
    "record_invoice_refund",
}
ALL_ACTIONS = READ_ACTIONS | MUTATING_ACTIONS

ACTION_LABELS = {
    "create_invoice": "Create draft invoice",
    "send_invoice": "Send invoice",
    "get_invoice": "Get invoice status",
    "list_invoices": "List invoices",
    "create_payment_link": "Create payment link",
    "refund_capture": "Refund a capture",
    "record_invoice_refund": "Record an external refund",
    "clarify": "Clarify",
}

REFUND_METHODS = {
    "BANK_TRANSFER",
    "CASH",
    "CHECK",
    "CREDIT_CARD",
    "DEBIT_CARD",
    "PAYPAL",
    "WIRE_TRANSFER",
    "OTHER",
}

SEEDED_INVOICE_ID = "INV2-MOCK-PAID-DEMO"
SEEDED_CAPTURE_ID = "CAPTURE-MOCK-DEMO"
SEEDED_CAPTURE_AMOUNT = Decimal("19.99")
SEEDED_CAPTURE_CURRENCY = "GBP"
MOCK_LINK_HOST = "mock.paypilot.invalid"
PAYMENT_TERMS = {
    "DUE_ON_RECEIPT",
    "DUE_ON_DATE_SPECIFIED",
    "NET_10",
    "NET_15",
    "NET_30",
    "NET_45",
    "NET_60",
    "NET_90",
    "NO_DUE_DATE",
}
