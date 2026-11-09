"""Regression tests for LLM fields that disagree with the user's message."""

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from paypilot.crosscheck import canonical_term, check_proposed_amount, extract_terms, signed_numbers
from paypilot.errors import ValidationError
from paypilot.models import IntentDraft
from tests.conftest import confirm, new_session, say


def test_signed_numbers_keep_the_minus_on_a_currency_code():
    assert signed_numbers("Invoice buyer@example.com -5 GBP for work") == [Decimal("-5")]
    assert signed_numbers("£-5") == [Decimal("-5")]
    assert signed_numbers("-£5 and £250 due in 14 days") == [
        Decimal("-5"),
        Decimal("250"),
        Decimal("14"),
    ]


def test_due_in_days_is_a_date_and_net_terms_stay_net():
    today = date(2026, 11, 7)
    terms = extract_terms("please invoice them, due in 14 days", today)
    assert terms is not None
    assert terms.term_type == "DUE_ON_DATE_SPECIFIED"
    assert terms.due_date == "2026-11-21"
    net = extract_terms("Net 30", today)
    assert net is not None
    assert net.term_type == "NET_30"
    assert net.due_date is None
    for phrase in ("due on receipt", "payable on receipt", "due now", "immediately"):
        receipt = extract_terms(f"Invoice them, {phrase}", today)
        assert receipt is not None
        assert receipt.term_type == "DUE_ON_RECEIPT"
    with pytest.raises(ValidationError):
        extract_terms("NET 30 and due in 14 days", today)


def test_negative_amount_in_the_message_is_never_a_positive_proposal(client):
    _stub(
        client,
        _invoice_draft(unit_amount="5.00", currency="GBP"),
    )
    session_id = new_session(client)
    response = say(client, session_id, "Invoice buyer@example.com -5 GBP for work")
    body = response.json()
    assert response.status_code == 200
    assert body["last_proposal"] is None
    assert body["activity"] == []
    assert body["messages"][-1]["body"] == "Amounts must be positive. PayPal was not called."
    assert client.app.state.book.gateway.calls == []


@pytest.mark.parametrize("amount", ["-5", "0", "0.00", "NaN", "Infinity"])
def test_non_positive_and_non_finite_amounts_are_refused(client, amount):
    _stub(client, _invoice_draft(unit_amount=amount, currency="GBP"))
    session_id = new_session(client)
    response = say(client, session_id, "Invoice buyer@example.com £250 for work")
    body = response.json()
    assert body["last_proposal"] is None
    assert body["activity"] == []
    assert "PayPal was not called." in body["messages"][-1]["body"]
    assert client.app.state.book.gateway.calls == []


def test_amount_that_is_not_in_the_message_is_refused(client):
    _stub(client, _invoice_draft(unit_amount="260.00", currency="GBP"))
    session_id = new_session(client)
    body = say(client, session_id, "Invoice buyer@example.com £250 for work").json()
    assert body["last_proposal"] is None
    assert "260.00" in body["messages"][-1]["body"]
    assert client.app.state.book.gateway.calls == []


@pytest.mark.parametrize(
    "payment_term",
    [
        None,
        {"term_type": "NET_30"},
        {"term_type": "DUE_ON_RECEIPT"},
        {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": "2020-01-01"},
    ],
)
def test_due_in_days_overrides_the_model_and_allows_confirm(client, payment_term):
    _stub(
        client,
        _invoice_draft(unit_amount="250.00", currency="GBP", payment_term=payment_term),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, due in 14 days"
    body = say(client, session_id, text).json()
    proposal = body["turn_proposal"]
    due = (date.today() + timedelta(days=14)).isoformat()
    assert proposal["confirm_blocked"] is False
    assert proposal["mismatches"] == []
    assert any(row["value"] == "due date from your message" for row in proposal["preview"])
    assert any(due == row["value"].removeprefix("Due on ").rstrip(".") for row in proposal["preview"])
    done = confirm(client, session_id, proposal["id"])
    assert done.status_code == 200
    term = done.json()["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due}


def test_due_in_days_is_what_paypal_would_be_sent(client):
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, due in 14 days"
    proposed = say(client, session_id, text).json()
    assert proposed["last_proposal"]["confirm_blocked"] is False
    due = (date.today() + timedelta(days=14)).isoformat()
    done = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    term = done["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "DUE_ON_DATE_SPECIFIED", "due_date": due}
    assert "_source_text" not in done["activity"][-1]["detail"]["request"]


def test_net_30_is_not_replaced_with_due_on_receipt(client):
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, net 30"
    proposed = say(client, session_id, text).json()
    assert proposed["last_proposal"]["confirm_blocked"] is False
    done = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    term = done["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "NET_30"}


def test_invented_net_60_is_discarded_when_the_message_states_no_terms(client):
    _stub(
        client,
        _invoice_draft(unit_amount="250.00", currency="GBP", payment_term={"term_type": "NET_60"}),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work"
    body = say(client, session_id, text).json()
    proposal = body["last_proposal"]
    assert proposal["confirm_blocked"] is False
    assert proposal["mismatches"] == []
    assert any(row["value"] == "terms: default (not stated)" for row in proposal["preview"])
    done = confirm(client, session_id, proposal["id"])
    assert done.status_code == 200
    term = done.json()["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "DUE_ON_RECEIPT"}


@pytest.mark.parametrize(
    "model_term",
    ["PAYABLE_ON_RECEIPT", "ON_RECEIPT", "IMMEDIATE", "IMMEDIATELY", "DUE_NOW", "DUE_UPON_RECEIPT"],
)
def test_receipt_synonyms_are_not_a_contradiction(client, model_term):
    _stub(
        client,
        _invoice_draft(
            unit_amount="250.00",
            currency="GBP",
            payment_term={"term_type": model_term},
        ),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, due on receipt"
    body = say(client, session_id, text).json()
    proposal = body["last_proposal"]
    assert proposal["confirm_blocked"] is False
    assert proposal["mismatches"] == []
    done = confirm(client, session_id, proposal["id"])
    assert done.status_code == 200
    term = done.json()["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "DUE_ON_RECEIPT"}


@pytest.mark.parametrize("model_term", ["NET30", "NET_30", "NET 30"])
def test_net_spellings_match_net_30(client, model_term):
    _stub(
        client,
        _invoice_draft(unit_amount="250.00", currency="GBP", payment_term={"term_type": model_term}),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, net 30"
    body = say(client, session_id, text).json()
    assert body["last_proposal"]["confirm_blocked"] is False
    assert body["last_proposal"]["mismatches"] == []


def test_payable_on_receipt_does_not_block_a_stated_due_date(client):
    _stub(
        client,
        _invoice_draft(
            unit_amount="250.00",
            currency="GBP",
            payment_term={"term_type": "PAYABLE_ON_RECEIPT"},
        ),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, due in 14 days"
    body = say(client, session_id, text).json()
    proposal = body["turn_proposal"]
    assert proposal["confirm_blocked"] is False
    assert any(row["value"] == "due date from your message" for row in proposal["preview"])
    assert confirm(client, session_id, proposal["id"]).status_code == 200


def test_term_synonyms_normalize():
    assert canonical_term("PAYABLE_ON_RECEIPT") == "DUE_ON_RECEIPT"
    assert canonical_term("NET30") == "NET_30"
    assert canonical_term("NET 30") == "NET_30"
    assert canonical_term("IMMEDIATE") == "DUE_ON_RECEIPT"


def test_net_30_contradicts_due_on_receipt(client):
    _stub(
        client,
        _invoice_draft(unit_amount="250.00", currency="GBP", payment_term={"term_type": "NET_30"}),
    )
    session_id = new_session(client)
    text = "Invoice buyer@example.com, Northwind Studio, £250 for website work, due on receipt"
    body = say(client, session_id, text).json()
    proposal = body["last_proposal"]
    assert proposal["confirm_blocked"] is False
    assert proposal["mismatches"] == []
    assert any("due on receipt" in row["value"] for row in proposal["preview"])
    done = confirm(client, session_id, proposal["id"])
    assert done.status_code == 200
    term = done.json()["activity"][-1]["detail"]["request"]["detail"]["payment_term"]
    assert term == {"term_type": "DUE_ON_RECEIPT"}


def test_invented_email_is_dropped_instead_of_offered(client):
    _stub(client, _invoice_draft(unit_amount="250.00", currency="GBP", email="ghost@example.com"))
    session_id = new_session(client)
    body = say(client, session_id, "Invoice Northwind £250 for website work").json()
    assert body["last_proposal"] is None
    assert "email" in body["messages"][-1]["body"].lower()
    assert "PayPal was not called." in body["messages"][-1]["body"]
    assert client.app.state.book.gateway.calls == []


def test_currency_and_email_mismatches_block_confirm(client):
    session_id = new_session(client)
    _stub(client, _invoice_draft(unit_amount="250.00", currency="USD", email="other@example.com"))
    body = say(
        client,
        session_id,
        "Invoice buyer@example.com, Northwind Studio, £250 for website work",
    ).json()
    proposal = body["last_proposal"]
    assert proposal["confirm_blocked"] is True
    blob = " ".join(proposal["mismatches"])
    assert "USD" in blob and "GBP" in blob
    assert "other@example.com" in blob
    assert confirm(client, session_id, proposal["id"]).status_code == 409
    assert client.app.state.book.gateway.calls == []


def test_refund_above_the_captured_amount_is_refused(client):
    _stub(
        client,
        IntentDraft(
            action="refund_capture",
            assistant_message="",
            fields={
                "capture_id": "CAPTURE-MOCK-DEMO",
                "currency_code": "GBP",
                "amount": "100.00",
                "note": "Refund from PayPilot",
            },
        ),
    )
    session_id = new_session(client)
    body = say(client, session_id, "Refund capture CAPTURE-MOCK-DEMO for £100").json()
    assert body["last_proposal"] is None
    assert "19.99" in body["messages"][-1]["body"]
    assert "PayPal was not called." in body["messages"][-1]["body"]
    assert client.app.state.book.gateway.calls == []


def test_refund_within_the_capture_can_be_confirmed(client):
    session_id = new_session(client)
    proposed = say(client, session_id, "Refund capture CAPTURE-MOCK-DEMO for £19.99").json()
    assert proposed["last_proposal"]["confirm_blocked"] is False
    done = confirm(client, session_id, proposed["last_proposal"]["id"])
    assert done.status_code == 200
    assert "MOCK-REFUND-" in done.json()["messages"][-1]["body"]


def test_negative_money_in_the_message_is_refused_before_the_model(client):
    class Boom:
        name = "boom"

        def parse(self, text, context):
            del text, context
            raise AssertionError("the model must not run")

    client.app.state.book.intent = Boom()
    session_id = new_session(client)
    for text in (
        "Invoice client@example.com for -5 GBP",
        "Invoice client@example.com for £-5",
        "Invoice client@example.com for -£5",
    ):
        body = say(client, session_id, text).json()
        assert body["turn_proposal"] is None
        assert body["messages"][-1]["body"] == "Amounts must be positive. PayPal was not called."
    assert client.app.state.book.gateway.calls == []


def test_email_alone_is_enough_for_an_invoice(client):
    _stub(
        client,
        _invoice_draft(unit_amount="120.00", currency="GBP", email="client@example.com", include_name=False),
    )
    session_id = new_session(client)
    text = "Invoice client@example.com for £120 for logo design, due in 14 days"
    body = say(client, session_id, text).json()
    proposal = body["turn_proposal"]
    assert proposal["confirm_blocked"] is False
    assert proposal["recipient_name"] == "Client"
    assert proposal["recipient_name_derived"] is True
    assert "Buyer name is required" not in body["messages"][-1]["body"]
    done = confirm(client, session_id, proposal["id"], {"recipient_name": "Ada Lovelace"})
    assert done.status_code == 200
    billing = done.json()["activity"][-1]["detail"]["request"]["primary_recipients"][0]["billing_info"]
    assert billing["email_address"] == "client@example.com"
    assert billing["name"]["given_name"] == "Ada"


def test_negative_amount_is_reported_before_a_missing_name(client):
    _stub(
        client,
        _invoice_draft(unit_amount="-5", currency="GBP", email="client@example.com", include_name=False),
    )
    session_id = new_session(client)
    body = say(client, session_id, "Invoice client@example.com for -5 GBP").json()
    message = body["messages"][-1]["body"]
    assert body["turn_proposal"] is None
    assert message == "Amounts must be positive. PayPal was not called."
    assert "Buyer name" not in message
    assert "Quantity" not in message
    assert client.app.state.book.gateway.calls == []


def test_magnitude_slip_is_corrected_to_the_only_stated_amount(client):
    _stub(
        client,
        IntentDraft(
            action="create_payment_link",
            assistant_message="",
            fields={
                "title": "logo consultation",
                "currency_code": "GBP",
                "unit_amount": "250.00",
                "reusable": False,
            },
        ),
    )
    session_id = new_session(client)
    body = say(client, session_id, "Create a payment link for £25 for a logo consultation").json()
    proposal = body["turn_proposal"]
    assert proposal["confirm_blocked"] is False
    assert proposal["mismatches"] == []
    assert any("Corrected from £250.00 to £25.00" in row["value"] for row in proposal["preview"])
    done = confirm(client, session_id, proposal["id"])
    assert done.status_code == 200
    sent = done.json()["activity"][-1]["detail"]["request"]["line_items"][0]["unit_amount"]["value"]
    assert sent == "25.00"


def test_a_later_request_supersedes_an_older_pending_proposal(client):
    _stub(client, _invoice_draft(unit_amount="120.00", currency="GBP", email="client@example.com"))
    session_id = new_session(client)
    first = say(client, session_id, "Invoice client@example.com for £120 for logo design").json()
    first_id = first["turn_proposal"]["id"]
    second = say(client, session_id, "Invoice client@example.com for £120 for a poster").json()
    assert second["turn_proposal"]["id"] != first_id
    assert second["turn_proposal"]["status"] == "pending"
    stale = next(message["proposal"] for message in second["messages"] if message["proposal"] and message["proposal"]["id"] == first_id)
    assert stale["status"] == "superseded"
    blocked = confirm(client, session_id, first_id)
    assert blocked.status_code == 409
    assert "replaced by a later request" in blocked.json()["error"]["message"]
    assert client.app.state.book.gateway.calls == []
    assert confirm(client, session_id, second["turn_proposal"]["id"]).status_code == 200


def test_ui_disables_superseded_cards_and_edits_buyer_name():
    script = Path("paypilot/static/app.js").read_text(encoding="utf-8")
    assert "Superseded by a later request" in script
    assert "recipient_name" in script
    assert "proposal.status === \"superseded\"" in script


def test_check_proposed_amount_rejects_a_flipped_sign():
    with pytest.raises(ValidationError) as caught:
        check_proposed_amount("5.00", "GBP", "Invoice buyer@example.com -5 GBP for work")
    assert "does not match" in caught.value.message
    assert "PayPal was not called." in caught.value.message


def _invoice_draft(
    unit_amount: str,
    currency: str,
    email: str = "buyer@example.com",
    payment_term=None,
    include_name: bool = True,
):
    fields = {
        "recipient_email": email,
        "currency_code": currency,
        "note": None,
        "items": [{"name": "logo design", "quantity": "1", "unit_amount": unit_amount}],
    }
    if include_name:
        fields["recipient_name"] = "Northwind Studio"
    if payment_term is not None:
        fields["payment_term"] = payment_term
    return IntentDraft(action="create_invoice", assistant_message="", fields=fields)


def _stub(client, draft: IntentDraft) -> None:
    class Stub:
        name = "stub"

        def parse(self, text, context):
            del text, context
            return draft

    client.app.state.book.intent = Stub()
