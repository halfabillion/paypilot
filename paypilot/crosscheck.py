"""Compare model fields with the user's own words.

Unsafe amounts are refused before a proposal exists. Other disagreements are
returned as mismatch strings so confirmation can be blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from paypilot.errors import ValidationError
from paypilot.models import PAYMENT_TERMS
from paypilot.money import display_money, format_money

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_NET = re.compile(r"\bNET[\s_-]?(10|15|30|45|60|90)\b", re.IGNORECASE)
_DUE_IN = re.compile(r"\bdue\s+in\s+(\d+)\s+days?\b", re.IGNORECASE)
_DUE_ON_RECEIPT = re.compile(
    r"\b(?:due\s+on\s+receipt|payable\s+on\s+receipt|due\s+now|immediately|DUE_ON_RECEIPT)\b",
    re.IGNORECASE,
)
_NON_FINITE = re.compile(r"nan|infinity|\binf\b", re.IGNORECASE)
_NUMBER = re.compile(
    r"""
    (?<![A-Za-z0-9.])
    (?:
        (?P<sign_a>[-+])?\s*(?P<sym>[£$€])\s*(?P<sign_b>[-+])?\s*
        (?P<num_a>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac_a>\d+))?
        |
        (?P<sign_c>[-+])?\s*(?P<num_b>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac_b>\d+))?
        \s*(?P<code>GBP|USD|EUR)\b
        |
        (?P<sign_d>[-+])?(?P<num_c>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac_c>\d+))?
    )
    (?![A-Za-z])
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SYMBOLS = {"£": "GBP", "$": "USD", "€": "EUR"}
_AMOUNT = re.compile(r"^-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class MoneyMention:
    value: Decimal
    currency: str | None


@dataclass(frozen=True)
class ExtractedTerms:
    term_type: str
    due_date: str | None
    label: str


def ground_proposal(
    action: str,
    payload: dict,
    text: str,
    model_fields: dict,
    *,
    today: date,
) -> tuple[dict, list[str], list[str]]:
    """Return payload, blocking mismatches, and non-blocking corrections."""
    grounded = dict(payload)
    mismatches: list[str] = []
    corrections: list[str] = []
    if action == "create_invoice":
        items = []
        for item in grounded["items"]:
            updated = dict(item)
            value, note = reconcile_amount(str(item["unit_amount"]), grounded["currency_code"], text)
            updated["unit_amount"] = format_money(value)
            if note:
                corrections.append(note)
            items.append(updated)
        grounded["items"] = items
        mismatches.extend(_currency_mismatches(grounded["currency_code"], text))
        mismatches.extend(_email_mismatches(grounded["recipient_email"], text))
        grounded["payment_term"], term_notes = resolve_terms(text, model_fields, today)
        corrections.extend(term_notes)
    elif action == "create_payment_link":
        value, note = reconcile_amount(str(grounded["unit_amount"]), grounded["currency_code"], text)
        grounded["unit_amount"] = format_money(value)
        if note:
            corrections.append(note)
        mismatches.extend(_currency_mismatches(grounded["currency_code"], text))
    elif action in {"refund_capture", "record_invoice_refund"}:
        value, note = reconcile_amount(str(grounded["amount"]), grounded["currency_code"], text)
        grounded["amount"] = format_money(value)
        if note:
            corrections.append(note)
        mismatches.extend(_currency_mismatches(grounded["currency_code"], text))
    if corrections:
        grounded["corrections"] = corrections
    return grounded, mismatches, corrections


def check_proposed_amount(raw_amount: str, currency: str, text: str) -> Decimal:
    value, _note = reconcile_amount(raw_amount, currency, text)
    return value


def reconcile_amount(raw_amount: str, currency: str, text: str) -> tuple[Decimal, str | None]:
    """Return the amount to store, and a correction note when a magnitude slip was fixed.

    A sign mismatch is still refused. A single stated amount wins when the model
    only slid the decimal point.
    """
    if not isinstance(raw_amount, str) or _NON_FINITE.search(raw_amount):
        raise ValidationError("Amount is not a finite number. PayPal was not called.")
    value = _signed_decimal(raw_amount)
    if not value.is_finite():
        raise ValidationError("Amount is not a finite number. PayPal was not called.")
    if value <= 0:
        shown = format_money(value) if _two_places(value) else raw_amount.strip()
        raise ValidationError(
            f"{shown} is not a valid amount. Use a value greater than zero. PayPal was not called."
        )
    if not _two_places(value):
        raise ValidationError(
            f"{raw_amount.strip()} has more than two decimal places. PayPal was not called."
        )
    if value > Decimal("1000000"):
        raise ValidationError("Amount exceeds 1000000.00. PayPal was not called.")
    mentions = money_mentions(text)
    if any(value == mention.value for mention in mentions):
        return value, None
    negative = [mention for mention in mentions if mention.value < 0]
    if any(value == -mention.value for mention in negative):
        readable = ", ".join(_signed_display(mention.value) for mention in mentions)
        raise ValidationError(
            f"{display_money(currency, value)} does not match a number in your message ({readable}). "
            "The sign and the value must both match. PayPal was not called."
        )
    positive = [mention for mention in mentions if mention.value > 0]
    numbers = signed_numbers(text)
    absent = not any(value == number for number in numbers)
    if len(positive) == 1 and absent and _magnitude_slip(positive[0].value, value):
        stated = positive[0].value
        note = (
            f"Corrected from {display_money(currency, value)} to {display_money(currency, stated)}. "
            f"{display_money(currency, stated)} is the only amount in your message."
        )
        return stated, note
    if absent:
        readable = ", ".join(_signed_display(number) for number in numbers) or "none"
        raise ValidationError(
            f"{display_money(currency, value)} does not match a number in your message ({readable}). "
            "The sign and the value must both match. PayPal was not called."
        )
    if mentions:
        readable = ", ".join(
            display_money(mention.currency, mention.value) if mention.currency else _signed_display(mention.value)
            for mention in mentions
        )
        raise ValidationError(
            f"{display_money(currency, value)} does not match the amount in your message ({readable}). "
            "PayPal was not called."
        )
    return value, None


def _magnitude_slip(stated: Decimal, proposed: Decimal) -> bool:
    """True when proposed is stated shifted by a power of ten."""
    if stated <= 0 or proposed <= 0 or stated == proposed:
        return False
    log = (proposed / stated).ln() / Decimal(10).ln()
    nearest = int(log.to_integral_value())
    if nearest == 0 or abs(nearest) > 4:
        return False
    scaled = stated * (Decimal(10) ** nearest)
    return scaled.quantize(Decimal("0.01")) == proposed.quantize(Decimal("0.01"))


def signed_numbers(text: str) -> list[Decimal]:
    return [mention.value for mention in _scan(text)]


def money_mentions(text: str) -> list[MoneyMention]:
    return [mention for mention in _scan(text) if mention.currency]


def message_has_negative_money(text: str) -> bool:
    """True when the raw message states a minus-signed money amount."""
    return any(mention.value < 0 for mention in money_mentions(text))


def extract_terms(text: str, today: date) -> ExtractedTerms | None:
    nets = list(dict.fromkeys(_NET.findall(text)))
    dues = list(dict.fromkeys(_DUE_IN.findall(text)))
    receipt = _DUE_ON_RECEIPT.search(text) is not None
    stated = sum(bool(item) for item in (nets, dues, receipt))
    if stated > 1 or len(nets) > 1 or len(dues) > 1:
        raise ValidationError(
            "Your message gives more than one payment term. PayPal was not called."
        )
    if dues:
        days = int(dues[0])
        if days < 1 or days > 365:
            raise ValidationError(
                "Due in N days must be between 1 and 365. PayPal was not called."
            )
        due = (today + timedelta(days=days)).isoformat()
        return ExtractedTerms("DUE_ON_DATE_SPECIFIED", due, f"due in {days} days ({due})")
    if nets:
        term = f"NET_{nets[0]}"
        return ExtractedTerms(term, None, term)
    if receipt:
        return ExtractedTerms("DUE_ON_RECEIPT", None, "due on receipt")
    return None


def resolve_terms(text: str, model_fields: dict, today: date) -> tuple[dict, list[str]]:
    extracted = extract_terms(text, today)
    model = model_payment_term(model_fields)
    if extracted is None:
        # The message did not state terms. The model's NET_* guess is not authoritative.
        return {"term_type": "DUE_ON_RECEIPT", "source": "default"}, []
    stored = {"term_type": extracted.term_type, "source": "message"}
    if extracted.due_date:
        stored["due_date"] = extracted.due_date
    corrections: list[str] = []
    # A stated due-in-N-days date is authoritative even when the model is silent,
    # names a NET term, or picks a different date. Confirm stays available.
    if extracted.due_date and (model is None or _terms_conflict(model, extracted)):
        corrections.append("due date from your message")
    elif model and _terms_conflict(model, extracted):
        corrections.append(
            f"Corrected to {extracted.label} from your message. The model said {_model_label(model)}."
        )
    return stored, corrections


_RECEIPT_ALIASES = {
    "DUE_ON_RECEIPT",
    "PAYABLE_ON_RECEIPT",
    "ON_RECEIPT",
    "DUE_UPON_RECEIPT",
    "PAYABLE_UPON_RECEIPT",
    "UPON_RECEIPT",
    "DUE_NOW",
    "DUE_IMMEDIATELY",
    "PAYABLE_IMMEDIATELY",
    "IMMEDIATE",
    "IMMEDIATELY",
}
_NET_DAYS = {"10", "15", "30", "45", "60", "90"}


def canonical_term(term: str) -> str:
    """Map model spellings onto the PayPal term they mean."""
    compact = re.sub(r"[^A-Z0-9]+", "_", term.upper()).strip("_")
    net = re.fullmatch(r"NET_?(\d{2})", compact)
    if net and net.group(1) in _NET_DAYS:
        return f"NET_{net.group(1)}"
    if compact in _RECEIPT_ALIASES:
        return "DUE_ON_RECEIPT"
    return compact


def model_payment_term(fields: dict) -> tuple[str, str | None] | None:
    raw = fields.get("payment_term", fields.get("term_type"))
    if raw in (None, ""):
        return None
    due_date = None
    if isinstance(raw, dict):
        term = str(raw.get("term_type") or "")
        due = raw.get("due_date")
        due_date = due.strip() if isinstance(due, str) and due.strip() else None
    else:
        term = str(raw)
    term = canonical_term(term)
    if not term or term in {"NULL", "NONE"}:
        return None
    if term not in PAYMENT_TERMS:
        return (term, due_date)
    return (term, due_date)


def _terms_conflict(model: tuple[str, str | None], extracted: ExtractedTerms) -> bool:
    term_type, due_date = model
    if term_type != extracted.term_type:
        return True
    if extracted.due_date and due_date and due_date != extracted.due_date:
        return True
    return False


def _model_label(model: tuple[str, str | None]) -> str:
    term_type, due_date = model
    if due_date:
        return f"{term_type} {due_date}"
    return term_type


def _currency_mismatches(currency: str, text: str) -> list[str]:
    found = text_currencies(text)
    if not found or currency in found:
        return []
    shown = ", ".join(sorted(found))
    return [
        f"Currency {currency} does not match {shown} in your message. "
        "Confirm is blocked. PayPal was not called."
    ]


def _email_mismatches(email: str, text: str) -> list[str]:
    found = _EMAIL.findall(text)
    folded = [item.casefold() for item in found]
    if email.casefold() in folded:
        return []
    if not found:
        raise ValidationError(
            "Include the buyer email address from your message. "
            "An email the message did not contain was dropped. PayPal was not called."
        )
    return [
        f"Email {email} does not match {', '.join(found)} in your message. "
        "Confirm is blocked. PayPal was not called."
    ]


def text_currencies(text: str) -> set[str]:
    found: set[str] = set()
    for mention in _scan(text):
        if mention.currency:
            found.add(mention.currency)
    for code in re.findall(r"\b(GBP|USD|EUR)\b", text, flags=re.IGNORECASE):
        found.add(code.upper())
    if "£" in text:
        found.add("GBP")
    if "$" in text:
        found.add("USD")
    if "€" in text:
        found.add("EUR")
    return found


def _scan(text: str) -> list[MoneyMention]:
    mentions: list[MoneyMention] = []
    for match in _NUMBER.finditer(text):
        if match.group("num_a"):
            negative = match.group("sign_a") == "-" or match.group("sign_b") == "-"
            value = _decimal(match.group("num_a"), match.group("frac_a"), negative)
            currency = _SYMBOLS[match.group("sym")]
        elif match.group("num_b"):
            negative = match.group("sign_c") == "-"
            value = _decimal(match.group("num_b"), match.group("frac_b"), negative)
            currency = match.group("code").upper()
        else:
            negative = match.group("sign_d") == "-"
            value = _decimal(match.group("num_c"), match.group("frac_c"), negative)
            currency = None
        mentions.append(MoneyMention(value, currency))
    return mentions


def _decimal(num: str, frac: str | None, negative: bool) -> Decimal:
    raw = num.replace(",", "")
    if frac:
        raw = f"{raw}.{frac}"
    value = Decimal(raw)
    return -value if negative else value


def _signed_decimal(raw: str) -> Decimal:
    text = raw.strip().replace(",", "")
    if not _AMOUNT.fullmatch(text):
        raise ValidationError(
            "Amount must be a plain decimal such as 250.00, without exponents. PayPal was not called."
        )
    return Decimal(text)


def _two_places(value: Decimal) -> bool:
    exponent = value.as_tuple().exponent
    return not isinstance(exponent, int) or exponent >= -2


def _signed_display(value: Decimal) -> str:
    return format_money(value)
