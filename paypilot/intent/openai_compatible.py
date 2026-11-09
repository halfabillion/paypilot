"""OpenAI-compatible chat completions adapter.

The model returns a JSON proposal. It does not receive PayPal credentials and it
cannot call PayPal. Temperature is 0 so a given prompt is as stable as the model allows.
"""

from __future__ import annotations

import json
import re

import httpx

from paypilot.errors import IntentError
from paypilot.models import IntentDraft
from paypilot.redaction import redact_text

_SYSTEM = """You are the intent parser for PayPilot, a bookkeeping assistant.
Return one JSON object and nothing else. You propose fields. You do not claim an invoice
was created, sent, paid, or refunded. You do not invent invoice ids, capture ids, statuses, or URLs.

Schema:
{
  "action": "create_invoice" | "send_invoice" | "get_invoice" | "list_invoices" | "create_payment_link" | "refund_capture" | "record_invoice_refund" | "clarify",
  "assistant_message": "short text used only when action is clarify",
  "missing": ["field names still needed"],
  "fields": {}
}

Field shapes:
- create_invoice.fields: recipient_email, recipient_name, currency_code (GBP, USD, or EUR), note or null, items: [{name, quantity, unit_amount}], payment_term: {term_type, due_date or null}

Keep the sign of every amount. "-5 GBP" stays negative and is not 5.
When the user states one amount, copy that amount exactly. Do not add or drop a zero.
recipient_name may be omitted when the user gives only an email.
Copy NET_10, NET_15, NET_30, NET_45, NET_60, NET_90, or "due in N days" into payment_term.
Use term_type DUE_ON_RECEIPT for due on receipt, payable on receipt, due now, and immediately.
Do not invent a separate PAYABLE_ON_RECEIPT value.
If the user states no payment terms, set payment_term to null. Do not invent NET_30, NET_60, or any other term.
- send_invoice.fields / get_invoice.fields / record_invoice_refund.fields: invoice_id. Use "LAST" when the user means the invoice already in the session. Copy an id only when it appears in the user message.
- create_payment_link.fields: title, currency_code, unit_amount, reusable (true or false)
- refund_capture.fields: capture_id, currency_code, amount, note. Copy capture_id only when it appears in the user message.
- record_invoice_refund.fields also includes currency_code, amount, method (BANK_TRANSFER, CASH, CHECK, CREDIT_CARD, DEBIT_CARD, PAYPAL, WIRE_TRANSFER, or OTHER).
- list_invoices.fields: {}
- clarify when a required field is missing. Amounts are decimal strings such as "250.00". Never use floats.

Recording an external refund is bookkeeping. Do not describe it as moving money.
"""


class OpenAICompatibleIntent:
    name = "openai"

    def __init__(self, *, http: httpx.Client, model: str, api_key: str, secrets: list[str]):
        self.http = http
        self.model = model
        self.api_key = api_key
        self.secrets = secrets

    def parse(self, text: str, context: dict) -> IntentDraft:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "message": text,
                            "session": {
                                "has_last_invoice": bool(context.get("last_invoice_id")),
                                "last_invoice_status": context.get("last_invoice_status"),
                                "mode": context.get("mode"),
                            },
                        }
                    ),
                },
            ],
        }
        try:
            response = self.http.post("/chat/completions", json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise IntentError("The AI provider timed out. PayPal was not called.") from exc
        except httpx.HTTPError as exc:
            raise IntentError("The AI provider was unreachable. PayPal was not called.") from exc
        if response.status_code == 401:
            raise IntentError("The AI provider rejected the API key (401). PayPal was not called.")
        if response.status_code >= 400:
            raise IntentError(
                redact_text(
                    f"The AI provider returned {response.status_code}. PayPal was not called.",
                    self.secrets,
                )
            )
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise IntentError("The AI provider returned an unreadable completion. PayPal was not called.") from exc
        if not isinstance(content, str):
            raise IntentError("The AI provider returned an unreadable completion. PayPal was not called.")
        return _draft_from_json(content, self.secrets)


def _draft_from_json(content: str, secrets: list[str]) -> IntentDraft:
    try:
        payload = _extract_json(content)
    except ValueError as exc:
        raise IntentError("The model did not return JSON. PayPal was not called.") from exc
    action = payload.get("action")
    if not isinstance(action, str):
        raise IntentError("The model omitted an action. PayPal was not called.")
    fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
    message = payload.get("assistant_message") if isinstance(payload.get("assistant_message"), str) else ""
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    missing_text = [str(item) for item in missing if isinstance(item, (str, int))]
    return IntentDraft(
        action=action.strip(),
        assistant_message=redact_text(message, secrets)[:1000],
        missing=missing_text,
        fields=fields,
    )


def _extract_json(content: str) -> dict:
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no json object")
        payload = json.loads(text[start : end + 1])
    if not isinstance(payload, dict):
        raise ValueError("json was not an object")
    return payload
