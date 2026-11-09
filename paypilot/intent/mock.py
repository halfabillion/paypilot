"""Deterministic intent parser. This is not a language model."""

from __future__ import annotations

import re

from paypilot.models import IntentDraft

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_INVOICE_ID = re.compile(r"\b(INV2-[A-Z0-9-]{4,40})\b", re.IGNORECASE)
_CAPTURE_ID = re.compile(r"\b(CAPTURE-[A-Z0-9-]{4,40})\b", re.IGNORECASE)
_MONEY = re.compile(
    r"(?P<sym>£|\$|€)\s*(?P<num>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"|(?P<num2>-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<code>GBP|USD|EUR)\b",
    re.IGNORECASE,
)
_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}


class MockIntent:
    name = "mock"

    def parse(self, text: str, context: dict) -> IntentDraft:
        del context  # The service binds "that invoice" from the session, not from the parser.
        cleaned = " ".join(text.strip().split())
        action = unambiguous_action(cleaned)
        if action == "record_invoice_refund":
            return _parse_record(cleaned)
        if action == "refund_capture":
            return _parse_capture(cleaned)
        if action == "create_payment_link":
            return _parse_link(cleaned, cleaned.lower())
        if action == "list_invoices":
            return IntentDraft(action="list_invoices", assistant_message="", fields={})
        if action == "get_invoice":
            return _parse_invoice_ref(cleaned, "get_invoice")
        if action == "send_invoice":
            return _parse_invoice_ref(cleaned, "send_invoice")
        if action == "create_invoice":
            return _parse_create(cleaned)
        return _clarify(
            "I can draft an invoice, send one, check status, list invoices, "
            "create a payment link, refund a capture, or record an external refund."
        )


def unambiguous_action(text: str) -> str | None:
    """The action the wording itself requires, when that wording is not ambiguous."""
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return None
    lowered = cleaned.lower()
    if _is_record_refund(lowered):
        return "record_invoice_refund"
    if _is_capture_refund(cleaned, lowered):
        return "refund_capture"
    if "payment link" in lowered or "pay link" in lowered:
        return "create_payment_link"
    if lowered.startswith("list invoices") or lowered in {"list my invoices", "show invoices"}:
        return "list_invoices"
    if re.search(r"\b(status|been paid|paid yet)\b", lowered):
        return "get_invoice"
    if re.search(r"\bsend\b", lowered) and re.search(r"\b(invoice|that|it)\b", lowered):
        return "send_invoice"
    if "invoice" in lowered:
        return "create_invoice"
    return None


def align_unambiguous_action(text: str, draft: IntentDraft) -> IntentDraft:
    """Keep model fields when the action matches. Replace a misrouted action."""
    forced = unambiguous_action(text)
    if forced is None or draft.action == forced:
        return draft
    parsed = MockIntent().parse(text, {})
    if parsed.action == forced:
        return parsed
    return draft


def _clarify(message: str) -> IntentDraft:
    return IntentDraft(action="clarify", assistant_message=message, fields={})


def _is_record_refund(lowered: str) -> bool:
    return "refund" in lowered and bool(re.search(r"\b(record|mark)\b", lowered))


def _is_capture_refund(text: str, lowered: str) -> bool:
    return "refund" in lowered and ("capture" in lowered or _CAPTURE_ID.search(text) is not None)


def _find_amount(text: str) -> dict | None:
    match = _MONEY.search(text)
    if not match:
        return None
    if match.group("sym"):
        raw = match.group("num").replace(",", "")
        currency = _SYMBOLS[match.group("sym")]
    else:
        raw = match.group("num2").replace(",", "")
        currency = match.group("code").upper()
    return {"raw": raw, "currency": currency}


def _invoice_id(text: str) -> str | None:
    match = _INVOICE_ID.search(text)
    return match.group(1).upper() if match else None


def _parse_invoice_ref(text: str, action: str) -> IntentDraft:
    invoice_id = _invoice_id(text) or "LAST"
    return IntentDraft(action=action, assistant_message="", fields={"invoice_id": invoice_id})


def _parse_create(text: str) -> IntentDraft:
    email_match = _EMAIL.search(text)
    amount = _find_amount(text)
    if email_match is None:
        return _clarify("Include the buyer email address. PayPal was not called.")
    if amount is None:
        return _clarify("Include an amount such as £250.00. PayPal was not called.")
    email = email_match.group(0)
    name_match = re.search(re.escape(email) + r"\s*,\s*([^,]+?)\s*,", text, re.IGNORECASE)
    if name_match:
        name = " ".join(name_match.group(1).split())
    else:
        local = email.split("@", 1)[0].replace(".", " ").replace("_", " ")
        name = local.title()
    item_match = re.search(r"\bfor\s+(.+)$", text, re.IGNORECASE)
    item = " ".join(item_match.group(1).split()).rstrip(".") if item_match else "Services"
    return IntentDraft(
        action="create_invoice",
        assistant_message="",
        fields={
            "recipient_email": email,
            "recipient_name": name,
            "currency_code": amount["currency"],
            "note": None,
            "items": [{"name": item[:120], "quantity": "1", "unit_amount": amount["raw"]}],
        },
    )


def _parse_link(text: str, lowered: str) -> IntentDraft:
    amount = _find_amount(text)
    if amount is None:
        return _clarify("Include an amount such as £19.99 for the payment link. PayPal was not called.")
    item_match = re.search(r"\bfor\s+(.+)$", text, re.IGNORECASE)
    title = " ".join(item_match.group(1).split()).rstrip(".") if item_match else "Payment"
    title = re.sub(r"^(the|a|an)\s+", "", title, flags=re.IGNORECASE)
    return IntentDraft(
        action="create_payment_link",
        assistant_message="",
        fields={
            "title": title[:120] or "Payment",
            "currency_code": amount["currency"],
            "unit_amount": amount["raw"],
            "reusable": "reusable" in lowered,
        },
    )


def _parse_capture(text: str) -> IntentDraft:
    capture = _CAPTURE_ID.search(text)
    amount = _find_amount(text)
    if capture is None:
        return _clarify("Name the capture id to refund. PayPilot will not invent one. PayPal was not called.")
    if amount is None:
        return _clarify("Include the refund amount, for example £19.99. PayPal was not called.")
    return IntentDraft(
        action="refund_capture",
        assistant_message="",
        fields={
            "capture_id": capture.group(1).upper(),
            "currency_code": amount["currency"],
            "amount": amount["raw"],
            "note": "Refund from PayPilot",
        },
    )


def _parse_record(text: str) -> IntentDraft:
    amount = _find_amount(text)
    if amount is None:
        return _clarify("Include the amount to record, for example £10. PayPal was not called.")
    lowered = text.lower()
    if re.search(r"\bcash\b", lowered):
        method = "CASH"
    elif re.search(r"\bcheck\b", lowered):
        method = "CHECK"
    elif re.search(r"\bbank\b", lowered):
        method = "BANK_TRANSFER"
    elif re.search(r"\bwire\b", lowered):
        method = "WIRE_TRANSFER"
    elif re.search(r"\bdebit\b", lowered):
        method = "DEBIT_CARD"
    elif re.search(r"\bcard\b", lowered):
        method = "CREDIT_CARD"
    elif re.search(r"\bpaypal\b", lowered):
        method = "PAYPAL"
    else:
        method = "OTHER"
    return IntentDraft(
        action="record_invoice_refund",
        assistant_message="",
        fields={
            "invoice_id": _invoice_id(text) or "LAST",
            "currency_code": amount["currency"],
            "amount": amount["raw"],
            "method": method,
        },
    )
