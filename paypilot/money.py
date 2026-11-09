"""Decimal money parsing. Amounts never travel as floats."""

from __future__ import annotations

import re
from decimal import Decimal

from paypilot.errors import ValidationError

ALLOWED_CURRENCIES = {"GBP", "USD", "EUR"}
CURRENCY_SYMBOLS = {"GBP": "£", "USD": "$", "EUR": "€"}
_AMOUNT = re.compile(r"^-?\d+(?:\.\d+)?$")
_MAX = Decimal("1000000")


def parse_currency(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValidationError("Currency must be a 3-letter code.")
    code = raw.strip().upper()
    if code not in ALLOWED_CURRENCIES:
        raise ValidationError("Currency must be GBP, USD, or EUR for this MVP.")
    return code


def parse_amount(raw: str) -> Decimal:
    """Accept a plain decimal string. Reject negatives, zero, and extra places."""
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise ValidationError("Amount must be a decimal string such as 250.00.")
    text = raw.strip().replace(",", "")
    if not _AMOUNT.fullmatch(text):
        raise ValidationError(
            "Amount must be a plain decimal such as 250.00, without exponents. PayPal was not called."
        )
    value = Decimal(text)
    if value <= 0:
        raise ValidationError(
            f"{format_money(value) if value == value.quantize(Decimal('0.01')) else text} "
            "is not a valid amount. Use a value greater than zero. PayPal was not called."
        )
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -2:
        raise ValidationError(
            f"{text} has more than two decimal places. PayPal was not called."
        )
    if value > _MAX:
        raise ValidationError("Amount exceeds 1000000.00. PayPal was not called.")
    return value.quantize(Decimal("0.01"))


def parse_quantity(raw: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise ValidationError("Quantity must be a whole number.")
    text = raw.strip()
    if not re.fullmatch(r"\d+", text):
        raise ValidationError("Quantity must be a whole number from 1 to 10000.")
    value = Decimal(text)
    if value < 1 or value > 10000:
        raise ValidationError("Quantity must be a whole number from 1 to 10000.")
    return value


def format_money(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')):.2f}"


def display_money(currency: str, value: Decimal | str) -> str:
    amount = value if isinstance(value, Decimal) else Decimal(value)
    symbol = CURRENCY_SYMBOLS.get(currency, "")
    rendered = format_money(amount)
    return f"{symbol}{rendered}" if symbol else f"{rendered} {currency}"
