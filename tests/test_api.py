import json

from tests.conftest import confirm, new_session, say


def test_invoice_send_and_status_are_separate_and_unpaid(client):
    session_id = new_session(client)
    opened = client.get(f"/api/sessions/{session_id}").json()
    assert opened["activity"] == []
    assert opened["mode"] == "mock"
    assert "MOCK" in opened["banner"]

    proposed = say(client, session_id, "Invoice buyer@example.com, Northwind Studio, £250 for website work")
    body = proposed.json()
    proposal = body["last_proposal"]
    assert proposal["status"] == "pending"
    assert proposal["action"] == "create_invoice"
    assert body["activity"] == []

    created = confirm(client, session_id, proposal["id"]).json()
    draft = created["activity"][-1]
    assert draft["path"] == "/v2/invoicing/invoices"
    assert draft["status_code"] == 201
    assert "MOCK" in draft["resource_id"]
    assert "has not been paid" in created["messages"][-1]["body"]
    invoice_id = draft["resource_id"]

    send = say(client, session_id, "Send that invoice").json()
    assert send["last_proposal"]["action"] == "send_invoice"
    assert len(send["activity"]) == 1
    sent = confirm(client, session_id, send["last_proposal"]["id"]).json()
    assert any(row["path"].endswith("/send") and row["status_code"] == 202 for row in sent["activity"])
    assert "unpaid" in sent["messages"][-1]["body"]
    assert "mock.paypilot.invalid" in sent["messages"][-1]["body"]

    status = say(client, session_id, "What is the status of that invoice?").json()
    text = status["messages"][-1]["body"]
    assert f"Invoice {invoice_id} status is SENT." in text
    assert "unpaid" in text
    assert "is PAID" not in text


def test_invalid_amount_and_reject_do_not_call_paypal(client):
    session_id = new_session(client)
    refused = say(client, session_id, "Invoice buyer@example.com £-5 for nothing")
    assert refused.status_code == 200
    assert "PayPal was not called." in refused.json()["messages"][-1]["body"]
    assert refused.json()["activity"] == []

    places = say(client, session_id, "Invoice buyer@example.com £12.345 for consulting")
    assert "two decimal places" in places.json()["messages"][-1]["body"]
    assert places.json()["activity"] == []

    proposed = say(client, session_id, "Invoice buyer@example.com, Acme, £10 for a logo").json()
    rejected = client.post(
        f"/api/sessions/{session_id}/proposals/{proposed['last_proposal']['id']}/reject"
    )
    assert rejected.status_code == 200
    assert rejected.json()["messages"][-1]["body"] == "Rejected. PayPal was not called."
    assert rejected.json()["activity"] == []
    calls = client.app.state.book.gateway.calls
    assert calls == []


def test_confirm_is_idempotent_and_sessions_are_isolated(client):
    first = new_session(client)
    second = new_session(client)
    proposed = say(client, first, "Invoice buyer@example.com, Northwind Studio, £250 for website work").json()
    proposal_id = proposed["last_proposal"]["id"]
    created = confirm(client, first, proposal_id).json()
    invoice_id = created["activity"][-1]["resource_id"]
    again = confirm(client, first, proposal_id)
    assert again.status_code == 200
    assert "not called again" in again.json()["messages"][-1]["body"]
    assert again.json()["activity"] == created["activity"]
    assert client.app.state.book.gateway.calls.count("create_draft_invoice") == 1

    leaked = say(client, second, f"What is the status of invoice {invoice_id}?")
    assert leaked.status_code == 404
    assert "not found in this session" in leaked.json()["error"]["message"]
    other = leaked.json()["session"]
    assert all(row["resource_id"] != invoice_id for row in other["activity"] if row["status_code"] == 200)
    own = say(client, second, "List invoices").json()
    assert invoice_id not in own["messages"][-1]["body"]
    assert "INV2-MOCK-PAID-DEMO" in own["messages"][-1]["body"]


def test_recorded_refund_is_not_a_capture_and_unpaid_invoices_reject_it(client):
    session_id = new_session(client)
    recorded = say(
        client,
        session_id,
        "Record an external refund of £10 on INV2-MOCK-PAID-DEMO",
    ).json()
    assert recorded["last_proposal"]["moves_money"] is False
    assert "does not transfer money" in recorded["last_proposal"]["summary"]
    done = confirm(client, session_id, recorded["last_proposal"]["id"]).json()
    assert "does not transfer money" in done["messages"][-1]["body"]
    assert done["activity"][-1]["moves_money"] is False
    assert done["activity"][-1]["path"].endswith("/refunds")

    proposed = say(client, session_id, "Invoice buyer@example.com, Northwind Studio, £40 for design").json()
    created = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    invoice_id = created["activity"][-1]["resource_id"]
    send = say(client, session_id, "Send that invoice").json()
    confirm(client, session_id, send["last_proposal"]["id"])
    attempt = say(client, session_id, f"Record an external refund of £10 on {invoice_id}").json()
    failed = confirm(client, session_id, attempt["last_proposal"]["id"])
    assert failed.status_code == 422
    assert "does not transfer money" in failed.json()["error"]["message"]
    status = say(client, session_id, f"What is the status of invoice {invoice_id}?").json()
    assert f"status is SENT" in status["messages"][-1]["body"]


def test_capture_refund_uses_the_named_fixture_only(client):
    session_id = new_session(client)
    missing = say(client, session_id, "Refund that capture for £19.99")
    assert missing.status_code == 200
    assert "will not invent" in missing.json()["messages"][-1]["body"].lower() or "Name the capture" in missing.json()["messages"][-1]["body"]
    assert missing.json()["activity"] == []

    proposed = say(client, session_id, "Refund capture CAPTURE-MOCK-DEMO for £19.99").json()
    assert "MOCK" in proposed["last_proposal"]["summary"] or "no money moves" in proposed["last_proposal"]["summary"]
    done = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    assert "MOCK-REFUND-" in done["messages"][-1]["body"]
    assert "no money moved at PayPal" in done["messages"][-1]["body"]


def test_payment_link_and_secrets_stay_out_of_the_feed(client, settings):
    session_id = new_session(client)
    proposed = say(client, session_id, "Give me a reusable £19.99 pay link for the workshop").json()
    done = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    row = done["activity"][-1]
    assert row["path"] == "/v1/checkout/payment-resources"
    assert row["detail"]["request"]["integration_mode"] == "LINK"
    assert row["detail"]["request"]["type"] == "BUY_NOW"
    assert row["detail"]["request"]["reusable"] == "MULTIPLE"
    assert isinstance(row["detail"]["request"]["line_items"][0]["unit_amount"]["value"], str)
    blob = json.dumps(done)
    assert settings.paypal_client_secret not in blob
    assert settings.openai_api_key not in blob
    assert "Bearer " not in blob

    echoed = say(client, session_id, f"Please note {settings.paypal_client_secret}").json()
    assert settings.paypal_client_secret not in json.dumps(echoed)


def test_cross_session_confirm_is_rejected(client):
    first = new_session(client)
    second = new_session(client)
    proposed = say(client, first, "Invoice buyer@example.com, Northwind Studio, £20 for copy").json()
    response = confirm(client, second, proposed["last_proposal"]["id"])
    assert response.status_code == 404
    assert client.app.state.book.gateway.calls == []


def test_hallucinated_invoice_id_is_not_used(client):
    from paypilot.models import IntentDraft

    class Inventing:
        name = "fixed"

        def parse(self, text, context):
            del text, context
            return IntentDraft(
                action="get_invoice",
                assistant_message="",
                fields={"invoice_id": "INV2-HALLUCINATED-ID"},
            )

    client.app.state.book.intent = Inventing()
    session_id = new_session(client)
    response = say(client, session_id, "How are you")
    assert response.status_code == 200
    assert "PayPal was not called." in response.json()["messages"][-1]["body"]
    assert response.json()["activity"] == []


def test_that_invoice_uses_the_session_cursor_not_the_model_id(client):
    from paypilot.models import IntentDraft

    session_id = new_session(client)
    proposed = say(client, session_id, "Invoice buyer@example.com, Northwind Studio, £250 for website work").json()
    created = confirm(client, session_id, proposed["last_proposal"]["id"]).json()
    invoice_id = created["activity"][-1]["resource_id"]

    class Inventing:
        name = "fixed"

        def parse(self, text, context):
            del text, context
            return IntentDraft(
                action="get_invoice",
                assistant_message="I invented a status.",
                fields={"invoice_id": "INV2-HALLUCINATED-ID"},
            )

    client.app.state.book.intent = Inventing()
    status = say(client, session_id, "What is the status of that invoice?").json()
    assert invoice_id in status["messages"][-1]["body"]
    assert "INV2-HALLUCINATED-ID" not in status["messages"][-1]["body"]
    assert "I invented a status" not in status["messages"][-1]["body"]


def test_rate_limit(tmp_path):
    from fastapi.testclient import TestClient

    from paypilot.config import Settings
    from paypilot.main import create_app

    app = create_app(
        Settings(
            database_path=str(tmp_path / "limit.sqlite"),
            rate_limit_per_minute=2,
        )
    )
    client = TestClient(app)
    session_id = new_session(client)
    assert say(client, session_id, "List invoices").status_code == 200
    assert say(client, session_id, "List invoices").status_code == 200
    limited = say(client, session_id, "List invoices")
    assert limited.status_code == 429


def test_ui_shell(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "PayPilot" in page.text
    assert "Draft proposal" in page.text
    assert client.get("/static/app.css").status_code == 200
    script = client.get("/static/app.js")
    assert script.status_code == 200
    assert "textContent" in script.text
    assert client.get("/favicon.ico").status_code == 204


def test_health_hides_secrets(client, settings):
    health = client.get("/api/health").json()
    assert health["paypal_mode"] == "mock"
    assert health["live_payments_enabled"] is False
    assert health["paypal_base_url"] == "https://api-m.sandbox.paypal.com"
    blob = json.dumps(health)
    assert settings.paypal_client_secret not in blob
    assert settings.paypal_client_id not in blob
