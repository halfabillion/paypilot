import pytest

from paypilot.errors import ValidationError
from paypilot.money import display_money, parse_amount, parse_currency, parse_quantity


def test_accepts_plain_decimal_and_rejects_float_shapes():
    assert str(parse_amount("250")) == "250.00"
    assert str(parse_amount("19.99")) == "19.99"
    assert str(parse_amount("1,250.50")) == "1250.50"
    assert display_money("GBP", parse_amount("250")) == "£250.00"


@pytest.mark.parametrize(
    "raw",
    ["-5", "0", "0.00", "12.345", "1e2", "250.001", "", "GBP", "1000000.01"],
)
def test_rejects_invalid_amounts(raw):
    with pytest.raises(ValidationError) as caught:
        parse_amount(raw)
    assert "PayPal was not called." in caught.value.message


def test_currency_and_quantity():
    assert parse_currency("gbp") == "GBP"
    with pytest.raises(ValidationError):
        parse_currency("JPY")
    assert str(parse_quantity("2")) == "2"
    with pytest.raises(ValidationError):
        parse_quantity("1.5")
    with pytest.raises(ValidationError):
        parse_quantity("0")
