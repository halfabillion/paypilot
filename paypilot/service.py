"""Proposal, confirmation, and audit. PayPal runs only after validation and, for writes, confirmation."""

from __future__ import annotations

import json
import re
import secrets
from datetime import date
from decimal import Decimal
from typing import Any

from paypilot.config import Settings
from paypilot.crosscheck import ground_proposal, message_has_negative_money
from paypilot.db import Database, utc_now
from paypilot.disclosure import MOCK_BANNER, PRODUCT_DISCLOSURE, SANDBOX_BANNER
from paypilot.errors import ConflictError, NotFoundError, PayPalAPIError, ValidationError
from paypilot.intent.mock import align_unambiguous_action
from paypilot.models import (
    ACTION_LABELS,
    ALL_ACTIONS,
    MUTATING_ACTIONS,
    READ_ACTIONS,
    REFUND_METHODS,
    SEEDED_CAPTURE_AMOUNT,
    SEEDED_CAPTURE_CURRENCY,
    SEEDED_CAPTURE_ID,
    CallTrace,
    CreateInvoiceRequest,
    CreatePaymentLinkRequest,
    IntentDraft,
    LineItem,
    RecordRefundRequest,
    RefundCaptureRequest,
)
from paypilot.money import display_money, format_money, parse_amount, parse_currency, parse_quantity
from paypilot.redaction import redact_obj, redact_text
from paypilot.status import plain_status

_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")
_INVOICE_ID = re.compile(r"\b(INV2-[A-Z0-9-]{4,40})\b", re.IGNORECASE)
_THAT_INVOICE = re.compile(r"\b(that|this|the|same)\s+invoice\b", re.IGNORECASE)
_TEXT_LIMIT = 2000

_CALLS = {
    "create_invoice": "POST /v2/invoicing/invoices",
    "send_invoice": "POST /v2/invoicing/invoices/{invoice_id}/send",
    "get_invoice": "GET /v2/invoicing/invoices/{invoice_id}",
    "list_invoices": "GET /v2/invoicing/invoices",
    "create_payment_link": "POST /v1/checkout/payment-resources",
    "refund_capture": "POST /v2/payments/captures/{capture_id}/refund",
    "record_invoice_refund": "POST /v2/invoicing/invoices/{invoice_id}/refunds",
}


class Bookkeeper:
    def __init__(self, db: Database, gateway, intent, settings: Settings):
        self.db = db
        self.gateway = gateway
        self.intent = intent
        self.settings = settings

    def meta(self) -> dict:
        return {
            "mode": self.settings.paypal_mode,
            "intent_provider": self.intent.name,
            "intent_label": _intent_label(self.intent, self.settings),
            "banner": MOCK_BANNER if self.settings.paypal_mode == "mock" else SANDBOX_BANNER,
            "disclosure": PRODUCT_DISCLOSURE,
            "live_payments_enabled": False,
            "sandbox_host": "api-m.sandbox.paypal.com",
        }

    def create_session(self) -> dict:
        session_id = "ses_" + secrets.token_hex(8)
        self.db.insert_session(session_id, self.settings.paypal_mode, self.intent.name)
        self.gateway.seed(session_id)
        return self.view(session_id)

    def view(self, session_id: str) -> dict:
        session = self._session(session_id)
        proposals = {
            message["proposal_id"]: self.db.get_proposal(message["proposal_id"])
            for message in self.db.list_messages(session_id)
            if message["proposal_id"]
        }
        messages = []
        for message in self.db.list_messages(session_id):
            proposal = proposals.get(message["proposal_id"]) if message["proposal_id"] else None
            messages.append(
                {
                    "id": message["id"],
                    "role": message["role"],
                    "body": message["body"],
                    "at": message["created_at"],
                    "proposal": _proposal_view(proposal) if proposal else None,
                }
            )
        activity = [_activity_view(row) for row in self.db.list_activity(session_id)]
        last_proposal = next(
            (message["proposal"] for message in reversed(messages) if message["proposal"]),
            None,
        )
        return {
            **self.meta(),
            "session_id": session["id"],
            "messages": messages,
            "activity": activity,
            "last_proposal": last_proposal,
        }

    def post_message(self, session_id: str, text: str) -> dict:
        self._session(session_id)
        if not isinstance(text, str) or not text.strip():
            raise ValidationError("Enter a request.")
        if len(text) > _TEXT_LIMIT:
            raise ValidationError("Keep the request under 2000 characters.")
        cleaned = redact_text(text.strip(), self.settings.secret_values())
        self.db.add_message(session_id, "user", cleaned, None)
        self.db.supersede_pending(session_id)
        if message_has_negative_money(cleaned):
            self.db.add_message(
                session_id,
                "assistant",
                "Amounts must be positive. PayPal was not called.",
                None,
            )
            return self._finish(session_id, None)
        draft = align_unambiguous_action(
            cleaned, self.intent.parse(cleaned, self._context(session_id))
        )
        if draft.action == "clarify" or draft.action not in ALL_ACTIONS:
            message = draft.assistant_message or (
                "I need a bookkeeping request with the buyer, the amount, and the action. "
                "PayPal was not called."
            )
            if draft.action not in ALL_ACTIONS and draft.action != "clarify":
                message = "I could not map that to a PayPal action. PayPal was not called."
            self.db.add_message(session_id, "assistant", self._scrub(message), None)
            return self._finish(session_id, None)
        try:
            payload = self._payload(draft, cleaned, session_id)
            payload, mismatches, corrections = ground_proposal(
                draft.action, payload, cleaned, draft.fields, today=date.today()
            )
            if draft.action == "refund_capture":
                self._check_capture_ceiling(
                    session_id,
                    payload["capture_id"],
                    payload["currency_code"],
                    Decimal(payload["amount"]),
                )
            payload["_source_text"] = cleaned
        except ValidationError as exc:
            self.db.add_message(session_id, "assistant", self._scrub(exc.message), None)
            return self._finish(session_id, None)
        proposal = self._new_proposal(session_id, draft.action, payload, mismatches, corrections)
        if draft.action in READ_ACTIONS:
            self._execute(proposal)
            return self._finish(session_id, proposal["id"])
        self.db.add_message(session_id, "assistant", proposal["summary"], proposal["id"])
        return self._finish(session_id, proposal["id"])

    def _finish(self, session_id: str, proposal_id: str | None) -> dict:
        """Session view plus the proposal created by this message, if any.

        `turn_proposal` is never an older proposal. Callers must not read
        `last_proposal` when they need the result of the message they just sent.
        """
        view = self.view(session_id)
        if proposal_id is None:
            view["turn_proposal"] = None
            return view
        row = self.db.get_proposal(proposal_id)
        view["turn_proposal"] = _proposal_view(row) if row else None
        return view

    def confirm(self, session_id: str, proposal_id: str, recipient_name: str | None = None) -> dict:
        proposal = self._owned_proposal(session_id, proposal_id)
        if proposal["status"] == "executed":
            self.db.add_message(
                session_id,
                "assistant",
                "This proposal was already confirmed. PayPal was not called again.",
                None,
            )
            return self.view(session_id)
        if proposal["status"] == "superseded":
            raise ConflictError(
                "This proposal was replaced by a later request. PayPal was not called."
            )
        if proposal["status"] == "rejected":
            raise ConflictError("This proposal was rejected. PayPal was not called.")
        if proposal["status"] == "executing":
            raise ConflictError("This proposal is already running.")
        if proposal["status"] not in {"pending", "failed"}:
            raise ConflictError("This proposal cannot be confirmed.")
        if not proposal["requires_confirmation"]:
            raise ConflictError("This proposal does not use confirmation.")
        if proposal.get("confirm_blocked"):
            raise ConflictError(
                "Confirm is blocked because the proposal does not match your message. "
                "PayPal was not called."
            )
        if recipient_name is not None and proposal["action"] == "create_invoice":
            self._apply_buyer_name(proposal, recipient_name)
            proposal = self._owned_proposal(session_id, proposal_id)
        self._recheck_before_confirm(proposal)
        self._execute(proposal)
        return self.view(session_id)

    def reject(self, session_id: str, proposal_id: str) -> dict:
        proposal = self._owned_proposal(session_id, proposal_id)
        if proposal["status"] == "executed":
            raise ConflictError("This proposal was already confirmed.")
        if proposal["status"] == "rejected":
            return self.view(session_id)
        if proposal["status"] == "executing":
            raise ConflictError("This proposal is already running.")
        self.db.update_proposal(proposal_id, status="rejected", resolved_at=utc_now())
        self.db.add_message(
            session_id,
            "assistant",
            "Rejected. PayPal was not called.",
            None,
        )
        return self.view(session_id)

    def _execute(self, proposal: dict) -> None:
        self.db.update_proposal(proposal["id"], status="executing")
        try:
            result, traces = self._dispatch(proposal)
        except PayPalAPIError as exc:
            self._record_traces(proposal, exc.traces)
            self.db.update_proposal(
                proposal["id"],
                status="failed",
                error=self._scrub(exc.message),
                resolved_at=utc_now(),
            )
            self.db.add_message(session_id_of(proposal), "assistant", self._scrub(exc.message), None)
            raise
        self._record_traces(proposal, traces)
        self.db.update_proposal(
            proposal["id"],
            status="executed",
            result_json=json.dumps(result),
            error=None,
            resolved_at=utc_now(),
        )
        self._remember_invoice(proposal["session_id"], proposal["action"], result)
        reply = self._scrub(describe_result(proposal["action"], result, self.settings.paypal_mode))
        attached = proposal["id"] if proposal["action"] in READ_ACTIONS else None
        # Writes already have a pending card. Reads attach the executed card to this reply.
        if proposal["action"] in MUTATING_ACTIONS:
            self.db.add_message(proposal["session_id"], "assistant", reply, None)
        else:
            self.db.add_message(proposal["session_id"], "assistant", reply, attached)

    def _dispatch(self, proposal: dict) -> tuple[dict, list[CallTrace]]:
        payload = json.loads(proposal["payload_json"])
        action = proposal["action"]
        session_id = proposal["session_id"]
        request_id = proposal["request_id"]
        if action == "create_invoice":
            return self.gateway.create_draft_invoice(
                _invoice_request(payload), request_id=request_id, session_id=session_id
            )
        if action == "send_invoice":
            return self.gateway.send_invoice(
                payload["invoice_id"], request_id=request_id, session_id=session_id
            )
        if action == "get_invoice":
            return self.gateway.get_invoice(payload["invoice_id"], session_id=session_id)
        if action == "list_invoices":
            return self.gateway.list_invoices(session_id=session_id)
        if action == "create_payment_link":
            return self.gateway.create_payment_link(
                _link_request(payload), request_id=request_id, session_id=session_id
            )
        if action == "refund_capture":
            return self.gateway.refund_capture(
                _capture_request(payload), request_id=request_id, session_id=session_id
            )
        if action == "record_invoice_refund":
            return self.gateway.record_invoice_refund(
                _record_request(payload), request_id=request_id, session_id=session_id
            )
        raise ValidationError("That action is not available. PayPal was not called.")

    def _payload(self, draft: IntentDraft, text: str, session_id: str) -> dict:
        fields = draft.fields
        if draft.action == "create_invoice":
            return self._create_payload(fields)
        if draft.action in {"send_invoice", "get_invoice"}:
            invoice_id = self._bind_invoice_id(str(fields.get("invoice_id") or ""), text, session_id)
            return {"invoice_id": invoice_id}
        if draft.action == "list_invoices":
            return {}
        if draft.action == "create_payment_link":
            return self._link_payload(fields)
        if draft.action == "refund_capture":
            return self._capture_payload(fields, text, session_id)
        if draft.action == "record_invoice_refund":
            return self._record_payload(fields, text, session_id)
        raise ValidationError("That action is not available. PayPal was not called.")

    def _create_payload(self, fields: dict) -> dict:
        items = fields.get("items")
        if not isinstance(items, list) or not items:
            raise ValidationError("Add at least one line item. PayPal was not called.")
        if len(items) > 20:
            raise ValidationError("Keep an invoice to 20 line items. PayPal was not called.")
        # Amounts are checked before name or email so a negative figure is the error the user sees.
        parsed_numbers = []
        for item in items:
            if not isinstance(item, dict):
                raise ValidationError("A line item is malformed. PayPal was not called.")
            quantity = parse_quantity(_decimal_input(item.get("quantity"), "Quantity"))
            unit = parse_amount(_decimal_input(item.get("unit_amount"), "Amount"))
            parsed_numbers.append((item, quantity, unit))
        email = _email(fields.get("recipient_email"))
        name, derived = _buyer_name(fields.get("recipient_name"), email)
        currency = parse_currency(str(fields.get("currency_code") or ""))
        parsed_items = []
        total = Decimal("0")
        for item, quantity, unit in parsed_numbers:
            item_name = _short_text(item.get("name"), "Line item", 120)
            total += quantity * unit
            parsed_items.append(
                {
                    "name": item_name,
                    "quantity": str(int(quantity)),
                    "unit_amount": format_money(unit),
                }
            )
        if total > Decimal("1000000"):
            raise ValidationError("Invoice total exceeds 1000000.00. PayPal was not called.")
        note = fields.get("note")
        note_text = None if note in (None, "") else _short_text(note, "Note", 400)
        return {
            "recipient_email": email,
            "recipient_name": name,
            "recipient_name_derived": derived,
            "currency_code": currency,
            "items": parsed_items,
            "note": note_text,
            "invoice_number": "PP-" + secrets.token_hex(4).upper(),
        }

    def _apply_buyer_name(self, proposal: dict, recipient_name: str) -> None:
        cleaned = " ".join(str(recipient_name).split())
        if len(cleaned) > 120:
            raise ValidationError("Buyer name is too long. PayPal was not called.")
        payload = json.loads(proposal["payload_json"])
        payload["recipient_name"] = cleaned
        payload["recipient_name_derived"] = False
        self.db.update_proposal(proposal["id"], payload_json=json.dumps(payload))

    def _link_payload(self, fields: dict) -> dict:
        currency = parse_currency(str(fields.get("currency_code") or ""))
        amount = parse_amount(_decimal_input(fields.get("unit_amount"), "Amount"))
        title = _short_text(fields.get("title"), "Payment link title", 120)
        reusable = fields.get("reusable")
        if not isinstance(reusable, bool):
            reusable = str(reusable).lower() == "true"
        return {
            "title": title,
            "currency_code": currency,
            "unit_amount": format_money(amount),
            "reusable": reusable,
        }

    def _capture_payload(self, fields: dict, text: str, session_id: str) -> dict:
        raw_id = str(fields.get("capture_id") or "")
        capture_id = _bind_capture_id(raw_id, text)
        currency = parse_currency(str(fields.get("currency_code") or ""))
        amount = parse_amount(_decimal_input(fields.get("amount"), "Amount"))
        note = fields.get("note")
        return {
            "capture_id": capture_id,
            "currency_code": currency,
            "amount": format_money(amount),
            "note": None if note in (None, "") else _short_text(note, "Note", 200),
        }

    def _record_payload(self, fields: dict, text: str, session_id: str) -> dict:
        invoice_id = self._bind_invoice_id(str(fields.get("invoice_id") or ""), text, session_id)
        currency = parse_currency(str(fields.get("currency_code") or ""))
        amount = parse_amount(_decimal_input(fields.get("amount"), "Amount"))
        method = str(fields.get("method") or "OTHER").upper()
        if method not in REFUND_METHODS:
            raise ValidationError(
                "Refund method must be one of the PayPal invoice methods "
                "(BANK_TRANSFER, CASH, CHECK, CREDIT_CARD, DEBIT_CARD, PAYPAL, WIRE_TRANSFER, OTHER). "
                "PayPal was not called."
            )
        return {
            "invoice_id": invoice_id,
            "currency_code": currency,
            "amount": format_money(amount),
            "method": method,
            "refund_date": date.today().isoformat(),
        }

    def _bind_invoice_id(self, draft_id: str, text: str, session_id: str) -> str:
        mentioned = [match.upper() for match in _INVOICE_ID.findall(text)]
        unique = list(dict.fromkeys(mentioned))
        if len(unique) > 1:
            raise ValidationError("Mention one invoice id at a time. PayPal was not called.")
        if len(unique) == 1:
            return unique[0]
        session = self._session(session_id)
        refers = draft_id == "LAST" or _THAT_INVOICE.search(text) is not None
        if refers:
            if session["last_invoice_id"]:
                return session["last_invoice_id"]
            raise ValidationError("There is no invoice in this session yet. PayPal was not called.")
        raise ValidationError(
            "Name the invoice, or say that invoice after creating one. "
            "An id the request did not contain was ignored. PayPal was not called."
        )

    def _check_capture_ceiling(
        self, session_id: str, capture_id: str, currency: str, amount: Decimal
    ) -> None:
        if self.settings.paypal_mode != "mock" or capture_id != SEEDED_CAPTURE_ID:
            return
        refunded = sum(
            (Decimal(item) for item in self.db.capture_refund_amounts(session_id, capture_id)),
            Decimal("0"),
        )
        remaining = SEEDED_CAPTURE_AMOUNT - refunded
        if currency != SEEDED_CAPTURE_CURRENCY:
            raise ValidationError(
                f"Capture {capture_id} is {SEEDED_CAPTURE_CURRENCY}. PayPal was not called."
            )
        if amount > remaining:
            raise ValidationError(
                f"{display_money(currency, amount)} is more than the "
                f"{display_money(SEEDED_CAPTURE_CURRENCY, remaining)} remaining on {capture_id}. "
                "PayPal was not called."
            )

    def _recheck_before_confirm(self, proposal: dict) -> None:
        payload = json.loads(proposal["payload_json"])
        source = payload.get("_source_text") or ""
        if not source:
            return
        try:
            _, mismatches, _corrections = ground_proposal(
                proposal["action"],
                {key: value for key, value in payload.items() if key != "_source_text"},
                source,
                {},
                today=date.today(),
            )
        except ValidationError as exc:
            raise ConflictError(exc.message) from exc
        if mismatches:
            raise ConflictError(
                "Confirm is blocked because the proposal does not match your message. "
                "PayPal was not called."
            )
        if proposal["action"] == "refund_capture":
            self._check_capture_ceiling(
                proposal["session_id"],
                payload["capture_id"],
                payload["currency_code"],
                Decimal(payload["amount"]),
            )

    def _new_proposal(
        self,
        session_id: str,
        action: str,
        payload: dict,
        mismatches: list[str],
        corrections: list[str] | None = None,
    ) -> dict:
        proposal_id = "prp_" + secrets.token_hex(8)
        corrections = corrections or []
        warnings = _warnings(action, self.settings.paypal_mode) + corrections + mismatches
        summary = _summary(action, payload, self.settings.paypal_mode)
        if corrections:
            summary = f"{summary} {' '.join(corrections)}"
        if mismatches:
            summary = f"{summary} {' '.join(mismatches)}"
        preview = _preview(action, payload)
        for note in corrections:
            preview.append({"label": "Correction", "value": note})
        paypal_call = _concrete_call(action, payload)
        row = {
            "id": proposal_id,
            "session_id": session_id,
            "action": action,
            "payload_json": json.dumps(payload),
            "summary": summary,
            "warnings_json": json.dumps(warnings),
            "preview_json": json.dumps(preview),
            "status": "pending",
            "requires_confirmation": 1 if action in MUTATING_ACTIONS else 0,
            "moves_money": 1 if action == "refund_capture" else 0,
            "paypal_call": paypal_call,
            "request_id": secrets.token_hex(16),
            "result_json": None,
            "error": None,
            "created_at": utc_now(),
            "resolved_at": None,
            "confirm_blocked": 1 if mismatches else 0,
            "mismatches_json": json.dumps(mismatches),
        }
        self.db.insert_proposal(row)
        return row

    def _record_traces(self, proposal: dict, traces: list[CallTrace]) -> None:
        secrets = self.settings.secret_values()
        for trace in traces:
            detail = redact_obj(
                {
                    "request": trace.request_body,
                    "response": trace.response_body,
                    "request_id": proposal["request_id"],
                    "mock": trace.mock,
                },
                secrets,
            )
            self.db.add_activity(
                {
                    "session_id": proposal["session_id"],
                    "proposal_id": proposal["id"],
                    "created_at": utc_now(),
                    "mode": "mock" if trace.mock else "sandbox",
                    "method": trace.method,
                    "path": trace.path,
                    "status_code": trace.status_code,
                    "resource_id": trace.resource_id,
                    "summary": self._scrub(trace.summary),
                    "detail_json": json.dumps(detail),
                    "moves_money": trace.moves_money,
                    "mock": trace.mock,
                    "debug_id": trace.debug_id,
                }
            )

    def _remember_invoice(self, session_id: str, action: str, result: dict) -> None:
        if action in {"create_invoice", "send_invoice", "get_invoice"} and result.get("id"):
            self.db.set_last_invoice(session_id, result["id"], result.get("status") or "")
        if action == "record_invoice_refund" and result.get("invoice_id"):
            self.db.set_last_invoice(
                session_id, result["invoice_id"], result.get("invoice_status") or ""
            )

    def _context(self, session_id: str) -> dict:
        session = self._session(session_id)
        return {
            "last_invoice_id": session["last_invoice_id"],
            "last_invoice_status": session["last_invoice_status"],
            "mode": self.settings.paypal_mode,
            "fixture_capture_id": SEEDED_CAPTURE_ID if self.settings.paypal_mode == "mock" else None,
        }

    def _session(self, session_id: str) -> dict:
        session = self.db.get_session(session_id)
        if session is None:
            raise NotFoundError("Session not found.")
        return session

    def _owned_proposal(self, session_id: str, proposal_id: str) -> dict:
        self._session(session_id)
        proposal = self.db.get_proposal(proposal_id)
        if proposal is None or proposal["session_id"] != session_id:
            raise NotFoundError("Proposal not found in this session.")
        return proposal

    def _scrub(self, text: str) -> str:
        return redact_text(text, self.settings.secret_values())


def session_id_of(proposal: dict) -> str:
    return proposal["session_id"]


def describe_result(action: str, result: dict, mode: str) -> str:
    mock_note = mode == "mock"
    if action == "create_invoice":
        text = (
            f"Draft {result['id']} is DRAFT. The buyer has not been notified, "
            "and the invoice has not been paid."
        )
        if mock_note:
            text += " This id is a mock fixture, not a PayPal sandbox invoice."
        return text
    if action == "send_invoice":
        if result.get("status") in {"SENT", "UNPAID"}:
            text = f"Invoice {result['id']} is {result['status']}, which means it is unpaid."
        else:
            text = f"Invoice {result['id']} is {result['status']}."
        text += f" Hosted link: {result.get('hosted_url') or 'not returned'}."
        if mock_note:
            text += " The link points at mock.paypilot.invalid and is not a PayPal-hosted page."
        return text
    if action == "get_invoice":
        text = f"Invoice {result['id']} status is {result['status']}. {result.get('status_plain') or plain_status(result['status'])}"
        if result.get("seeded"):
            text += " This row is a seeded fixture, not a sandbox payment."
        elif mock_note:
            text += " Status was read from the mock ledger."
        return text
    if action == "list_invoices":
        lines = [f"{len(result['invoices'])} invoices in this session."]
        for invoice in result["invoices"]:
            kind = "seeded fixture, not a sandbox payment" if invoice.get("seeded") else (
                "mock fixture" if invoice.get("mock") else "sandbox invoice"
            )
            lines.append(
                f"{invoice['id']} {invoice['status']} — {kind} — {invoice.get('total_display') or ''} — {invoice.get('item_summary') or ''}"
            )
        return " ".join(lines) if len(lines) == 1 else "\n".join(lines)
    if action == "create_payment_link":
        text = (
            f"Payment link {result['id']} for {result['title']} is {result['status']}. "
            f"URL: {result['payment_link']}."
        )
        if result.get("mock"):
            text += " This URL is a fixture, not a PayPal-hosted page."
        return text
    if action == "refund_capture":
        text = f"Refund {result['id']} for capture {result['capture_id']} is {result['status']}."
        if result.get("mock"):
            text += " This is a mock refund id. No PayPal sandbox refund was created, and no money moved at PayPal."
        else:
            text += " This is a sandbox capture refund of test funds."
        return text
    if action == "record_invoice_refund":
        return (
            f"Recorded refund {result['id']} on invoice {result['invoice_id']}. "
            f"Invoice status is now {result['invoice_status']}. "
            "Recording an external refund does not transfer money."
        )
    return "The PayPal call finished."


def _summary(action: str, payload: dict, mode: str) -> str:
    mock = mode == "mock"
    if action == "create_invoice":
        item = payload["items"][0]
        total = display_money(payload["currency_code"], _total(payload))
        return (
            f"Draft invoice for {payload['recipient_name']} ({payload['recipient_email']}): "
            f"{item['quantity']} × {item['name']} at {display_money(payload['currency_code'], item['unit_amount'])}. "
            f"Total {total}. {_terms_phrase(payload)} Confirm to create a draft. Sending is a separate step."
            + (" Mock mode writes a fixture, not a sandbox invoice." if mock else "")
        )
    if action == "send_invoice":
        return (
            f"Send invoice {payload['invoice_id']} to the buyer. "
            "Confirm to call PayPal send. This does not mark the invoice paid."
            + (" Mock mode does not email anyone." if mock else "")
        )
    if action == "get_invoice":
        return f"Read invoice {payload['invoice_id']} from the {'mock ledger' if mock else 'PayPal sandbox'}."
    if action == "list_invoices":
        return "List invoices in this session."
    if action == "create_payment_link":
        reuse = "Reusable" if payload["reusable"] else "Single-use"
        return (
            f"{reuse} payment link for {payload['title']} at "
            f"{display_money(payload['currency_code'], payload['unit_amount'])}. "
            "Confirm to create it."
            + (" Mock mode returns a mock.paypilot.invalid URL." if mock else "")
        )
    if action == "refund_capture":
        return (
            f"Refund capture {payload['capture_id']} for "
            f"{display_money(payload['currency_code'], payload['amount'])}. "
            "Confirm to call Payments v2."
            + (
                f" Mock mode only refunds the seeded fixture {SEEDED_CAPTURE_ID}, and no money moves at PayPal."
                if mock
                else " Sandbox mode refunds a test-fund capture."
            )
        )
    if action == "record_invoice_refund":
        return (
            f"Record an external {payload['method']} refund of "
            f"{display_money(payload['currency_code'], payload['amount'])} on {payload['invoice_id']}. "
            "Recording an external refund does not transfer money."
        )
    return action


def _warnings(action: str, mode: str) -> list[str]:
    mock = mode == "mock"
    warnings = []
    if action in MUTATING_ACTIONS:
        warnings.append(
            "PayPal is called only after you confirm."
            if not mock
            else "Nothing is sent to PayPal. Confirm stores a labeled fixture."
        )
    if action == "create_invoice":
        warnings.append("This creates a draft only. The buyer is notified only if you later confirm a send.")
    elif action == "send_invoice":
        warnings.append("Sending does not mark the invoice paid.")
    elif action == "create_payment_link":
        warnings.append(
            "Payment Links must be enabled on the sandbox REST app. "
            "A 403 means that product is not available to the app."
        )
    elif action == "refund_capture":
        warnings.append("This asks Payments v2 to refund a captured payment.")
        if mock:
            warnings.append(
                f"The seeded fixture is {SEEDED_CAPTURE_ID} for £19.99. "
                "A mock refund id is not a sandbox refund."
            )
    elif action == "record_invoice_refund":
        warnings.append(
            "Recording an external refund does not transfer money. It is not a capture refund."
        )
    elif action in READ_ACTIONS:
        warnings.append("Read-only. The status is the gateway response, not a guess.")
    return warnings


def _preview(action: str, payload: dict) -> list[dict[str, str]]:
    rows = [{"label": "PayPal call", "value": _concrete_call(action, payload)}]
    if action == "create_invoice":
        rows.extend(
            [
                {
                    "label": "Buyer",
                    "value": f"{payload['recipient_name']} <{payload['recipient_email']}>",
                },
                {
                    "label": "Line",
                    "value": ", ".join(
                        f"{item['quantity']} × {item['name']}" for item in payload["items"]
                    ),
                },
                {"label": "Total", "value": display_money(payload["currency_code"], _total(payload))},
                {"label": "Terms", "value": _terms_phrase(payload).strip()},
            ]
        )
    elif action in {"send_invoice", "get_invoice"}:
        rows.append({"label": "Invoice", "value": payload["invoice_id"]})
    elif action == "create_payment_link":
        rows.extend(
            [
                {"label": "Title", "value": payload["title"]},
                {
                    "label": "Amount",
                    "value": display_money(payload["currency_code"], payload["unit_amount"]),
                },
                {"label": "Reusable", "value": "MULTIPLE" if payload["reusable"] else "omitted"},
            ]
        )
    elif action == "refund_capture":
        rows.extend(
            [
                {"label": "Capture", "value": payload["capture_id"]},
                {
                    "label": "Amount",
                    "value": display_money(payload["currency_code"], payload["amount"]),
                },
            ]
        )
    elif action == "record_invoice_refund":
        rows.extend(
            [
                {"label": "Invoice", "value": payload["invoice_id"]},
                {
                    "label": "Amount",
                    "value": display_money(payload["currency_code"], payload["amount"]),
                },
                {"label": "Method", "value": payload["method"]},
                {"label": "Moves money", "value": "No. This records a refund only."},
            ]
        )
    return rows


def _concrete_call(action: str, payload: dict) -> str:
    template = _CALLS[action]
    return template.format(
        invoice_id=payload.get("invoice_id", ""),
        capture_id=payload.get("capture_id", ""),
    )


def _total(payload: dict) -> Decimal:
    total = Decimal("0")
    for item in payload["items"]:
        total += Decimal(item["quantity"]) * Decimal(item["unit_amount"])
    return total


def _proposal_view(row: dict) -> dict:
    result = json.loads(row["result_json"]) if row["result_json"] else None
    payload = json.loads(row["payload_json"]) if row.get("payload_json") else {}
    return {
        "id": row["id"],
        "action": row["action"],
        "action_label": ACTION_LABELS.get(row["action"], row["action"]),
        "status": row["status"],
        "summary": row["summary"],
        "warnings": json.loads(row["warnings_json"]),
        "preview": json.loads(row["preview_json"]),
        "requires_confirmation": bool(row["requires_confirmation"]),
        "moves_money": bool(row["moves_money"]),
        "confirm_blocked": bool(row.get("confirm_blocked")),
        "mismatches": json.loads(row.get("mismatches_json") or "[]"),
        "paypal_call": row["paypal_call"],
        "result": result,
        "error": row["error"],
        "recipient_name": payload.get("recipient_name", "") if row["action"] == "create_invoice" else None,
        "recipient_name_derived": bool(payload.get("recipient_name_derived"))
        if row["action"] == "create_invoice"
        else False,
    }


def _activity_view(row: dict) -> dict:
    return {
        "id": row["id"],
        "at": row["created_at"],
        "mode": row["mode"],
        "method": row["method"],
        "path": row["path"],
        "status_code": row["status_code"],
        "resource_id": row["resource_id"],
        "summary": row["summary"],
        "moves_money": bool(row["moves_money"]),
        "mock": bool(row["mock"]),
        "debug_id": row["debug_id"],
        "detail": json.loads(row["detail_json"]),
    }


def _terms_phrase(payload: dict) -> str:
    term = payload.get("payment_term") or {}
    if term.get("source") == "default":
        return "terms: default (not stated)"
    term_type = term.get("term_type") or "DUE_ON_RECEIPT"
    if term_type == "DUE_ON_DATE_SPECIFIED" and term.get("due_date"):
        return f"Due on {term['due_date']}."
    if term_type != "DUE_ON_RECEIPT":
        return f"Terms {term_type}."
    return "Due on receipt."


def _intent_label(intent, settings: Settings) -> str:
    if intent.name == "openai":
        return f"OpenAI-compatible chat completions ({settings.openai_model})"
    return "Deterministic parser"


def _buyer_name(raw: Any, email: str) -> tuple[str, bool]:
    """Return a display name and whether it was derived from the email local part."""
    if isinstance(raw, str) and raw.strip():
        text = " ".join(raw.split())
        if len(text) > 120:
            raise ValidationError("Buyer name is too long. PayPal was not called.")
        return text, False
    local = email.split("@", 1)[0].replace(".", " ").replace("_", " ").replace("-", " ")
    local = " ".join(part for part in local.split() if part)
    name = local.title() if local else "Buyer"
    return name[:120], True


def _email(raw: Any) -> str:
    if not isinstance(raw, str) or not _EMAIL.fullmatch(raw.strip()) or len(raw) > 254:
        raise ValidationError("Enter a buyer email address. PayPal was not called.")
    return raw.strip()


def _short_text(raw: Any, label: str, limit: int) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError(f"{label} is required. PayPal was not called.")
    text = " ".join(raw.split())
    if len(text) > limit:
        raise ValidationError(f"{label} is too long. PayPal was not called.")
    return text


def _invoice_request(payload: dict) -> CreateInvoiceRequest:
    return CreateInvoiceRequest(
        recipient_email=payload["recipient_email"],
        recipient_name=payload["recipient_name"],
        currency_code=payload["currency_code"],
        items=[
            LineItem(
                name=item["name"],
                quantity=Decimal(item["quantity"]),
                unit_amount=Decimal(item["unit_amount"]),
            )
            for item in payload["items"]
        ],
        note=payload.get("note"),
        invoice_number=payload["invoice_number"],
        payment_term=payload.get("payment_term"),
    )


def _link_request(payload: dict) -> CreatePaymentLinkRequest:
    return CreatePaymentLinkRequest(
        title=payload["title"],
        currency_code=payload["currency_code"],
        unit_amount=Decimal(payload["unit_amount"]),
        reusable=bool(payload["reusable"]),
    )


def _capture_request(payload: dict) -> RefundCaptureRequest:
    return RefundCaptureRequest(
        capture_id=payload["capture_id"],
        currency_code=payload["currency_code"],
        amount=Decimal(payload["amount"]),
        note=payload.get("note"),
    )


def _record_request(payload: dict) -> RecordRefundRequest:
    return RecordRefundRequest(
        invoice_id=payload["invoice_id"],
        currency_code=payload["currency_code"],
        amount=Decimal(payload["amount"]),
        method=payload["method"],
        refund_date=payload["refund_date"],
    )


def _decimal_input(raw: Any, label: str) -> str:
    if isinstance(raw, str):
        return raw
    if isinstance(raw, int) and not isinstance(raw, bool):
        return str(raw)
    raise ValidationError(f"{label} must be a decimal string. PayPal was not called.")


def _bind_capture_id(draft_id: str, text: str) -> str:
    match = re.search(r"\b(CAPTURE-[A-Z0-9-]{4,40})\b", text, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    candidate = draft_id.strip()
    if candidate and candidate.upper() != "LAST" and candidate in text:
        if not re.fullmatch(r"[A-Za-z0-9-]{8,40}", candidate):
            raise ValidationError("That capture id is not a usable token. PayPal was not called.")
        return candidate
    raise ValidationError(
        "Name the capture id to refund. PayPilot will not invent one. PayPal was not called."
    )
