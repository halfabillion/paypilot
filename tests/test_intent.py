from paypilot.intent.mock import MockIntent


def draft(text: str):
    return MockIntent().parse(text, {})


def test_demo_phrases():
    created = draft("Invoice buyer@example.com, Northwind Studio, £250 for website work")
    assert created.action == "create_invoice"
    assert created.fields["recipient_email"] == "buyer@example.com"
    assert created.fields["recipient_name"] == "Northwind Studio"
    assert created.fields["currency_code"] == "GBP"
    assert created.fields["items"][0]["unit_amount"] == "250"
    assert created.fields["items"][0]["name"] == "website work"

    assert draft("Send that invoice").fields["invoice_id"] == "LAST"
    assert draft("What is the status of that invoice?").action == "get_invoice"
    paid = draft("Has invoice INV2-MOCK-PAID-DEMO been paid?")
    assert paid.action == "get_invoice"
    assert paid.fields["invoice_id"] == "INV2-MOCK-PAID-DEMO"

    link = draft("Give me a reusable £19.99 pay link for the workshop")
    assert link.action == "create_payment_link"
    assert link.fields["unit_amount"] == "19.99"
    assert link.fields["title"] == "workshop"
    assert link.fields["reusable"] is True

    refund = draft("Refund capture CAPTURE-MOCK-DEMO for £19.99")
    assert refund.action == "refund_capture"
    assert refund.fields["capture_id"] == "CAPTURE-MOCK-DEMO"

    record = draft("Record an external refund of £10 on INV2-MOCK-PAID-DEMO")
    assert record.action == "record_invoice_refund"
    assert record.fields["method"] == "OTHER"
    assert record.fields["amount"] == "10"

    cash = draft("Mark a refund of £10 cash on INV2-MOCK-PAID-DEMO")
    assert cash.action == "record_invoice_refund"
    assert cash.fields["method"] == "CASH"
    assert draft("List invoices").action == "list_invoices"


def test_missing_email_does_not_invent_one():
    missing = draft("Invoice Acme £250 for website work")
    assert missing.action == "clarify"
    assert "email" in missing.assistant_message.lower()


def test_negative_amount_is_left_for_validation():
    created = draft("Invoice buyer@example.com £-5 for nothing")
    assert created.action == "create_invoice"
    assert created.fields["items"][0]["unit_amount"] == "-5"
