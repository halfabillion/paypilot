import json

import httpx
import pytest

from paypilot.config import SANDBOX_BASE_URL, Settings, validate_paypal_base_url
from paypilot.errors import ConfigError, PayPalAPIError
from paypilot.main import create_app
from paypilot.models import (
    CreateInvoiceRequest,
    CreatePaymentLinkRequest,
    LineItem,
    RecordRefundRequest,
    RefundCaptureRequest,
)
from decimal import Decimal


SECRET = "sandbox-secret-value"
CLIENT_ID = "sandbox-client-id"
TOKEN = "sandbox-access-token-test"


def _settings(tmp_path, **overrides):
    data = dict(
        paypal_mode="sandbox",
        intent_provider="mock",
        paypal_client_id=CLIENT_ID,
        paypal_client_secret=SECRET,
        paypal_base_url=SANDBOX_BASE_URL,
        database_path=str(tmp_path / "sandbox.sqlite"),
        rate_limit_per_minute=0,
        invoicer_email="merchant@example.com",
    )
    data.update(overrides)
    return Settings(**data)


def test_live_hosts_and_missing_credentials_fail_closed(tmp_path):
    for url in (
        "https://api-m.paypal.com",
        "https://api.paypal.com",
        "http://api-m.sandbox.paypal.com",
        "https://api-m.sandbox.paypal.com.evil.example",
        "https://user:secret@api-m.sandbox.paypal.com",
    ):
        with pytest.raises(ConfigError):
            validate_paypal_base_url(url)
    with pytest.raises(ConfigError):
        create_app(_settings(tmp_path, paypal_client_id="", paypal_client_secret=""))
    with pytest.raises(ConfigError):
        create_app(_settings(tmp_path, paypal_base_url="https://api-m.paypal.com"))


def test_sandbox_shapes_token_cache_and_redaction(tmp_path):
    state = {"tokens": 0, "invoices": 0, "expires_in": 3600}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api-m.sandbox.paypal.com"
        assert request.url.scheme == "https"
        if request.url.path == "/v1/oauth2/token":
            state["tokens"] += 1
            decoded = request.headers["Authorization"]
            assert decoded.startswith("Basic ")
            import base64

            raw = base64.b64decode(decoded.split()[1]).decode()
            assert raw == f"{CLIENT_ID}:{SECRET}"
            assert b"grant_type=client_credentials" in request.content
            return httpx.Response(
                200,
                json={"access_token": TOKEN, "expires_in": state["expires_in"], "token_type": "Bearer"},
            )
        assert request.headers["Authorization"] == f"Bearer {TOKEN}"
        assert "Basic " not in request.headers["Authorization"]
        if request.url.path == "/v2/invoicing/invoices" and request.method == "POST":
            state["invoices"] += 1
            body = json.loads(request.content)
            assert body["detail"]["currency_code"] == "GBP"
            assert body["items"][0]["unit_amount"]["value"] == "250.00"
            assert isinstance(body["items"][0]["unit_amount"]["value"], str)
            assert body["primary_recipients"][0]["billing_info"]["email_address"] == "buyer@example.com"
            assert request.headers["PayPal-Request-Id"]
            return httpx.Response(
                201,
                json={
                    "id": "INV2-SANDBOX-001",
                    "status": "DRAFT",
                    "detail": {"invoice_number": body["detail"]["invoice_number"], "currency_code": "GBP"},
                    "due_amount": {"currency_code": "GBP", "value": "250.00"},
                },
            )
        if request.url.path == "/v2/invoicing/invoices/INV2-SANDBOX-001/send":
            assert json.loads(request.content)["send_to_recipient"] is True
            return httpx.Response(202, json={})
        if request.url.path == "/v2/invoicing/invoices/INV2-SANDBOX-001" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "INV2-SANDBOX-001",
                    "status": "SENT",
                    "detail": {
                        "metadata": {
                            "recipient_view_url": "https://www.sandbox.paypal.com/invoice/p/#INV2-SANDBOX-001"
                        }
                    },
                    "due_amount": {"currency_code": "GBP", "value": "250.00"},
                },
            )
        if request.url.path == "/v2/invoicing/invoices" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"id": "INV2-SANDBOX-001", "status": "SENT"}]})
        if request.url.path == "/v1/checkout/payment-resources":
            body = json.loads(request.content)
            assert body["integration_mode"] == "LINK"
            assert body["type"] == "BUY_NOW"
            assert body["reusable"] == "MULTIPLE"
            return httpx.Response(
                201,
                json={
                    "id": "PLB-SANDBOX-1",
                    "status": "ACTIVE",
                    "links": [
                        {
                            "rel": "payment_link",
                            "href": "https://www.sandbox.paypal.com/ncp/payment/PLB-SANDBOX-1",
                            "method": "GET",
                        }
                    ],
                },
            )
        if request.url.path == "/v2/payments/captures/2GG279541U471931P/refund":
            body = json.loads(request.content)
            assert body["amount"]["value"] == "19.99"
            return httpx.Response(201, json={"id": "1JU08902781691411", "status": "COMPLETED"})
        if request.url.path == "/v2/invoicing/invoices/INV2-SANDBOX-001/refunds":
            body = json.loads(request.content)
            assert body["method"] == "OTHER"
            assert body["amount"]["value"] == "10.00"
            return httpx.Response(200, json={"refund_id": "EXTR-2LG703375E477444T"})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    app = create_app(_settings(tmp_path), paypal_transport=httpx.MockTransport(handler))
    gateway = app.state.book.gateway
    session = app.state.book.create_session()["session_id"]
    invoice, traces = gateway.create_draft_invoice(
        CreateInvoiceRequest(
            recipient_email="buyer@example.com",
            recipient_name="Northwind Studio",
            currency_code="GBP",
            items=[LineItem(name="website work", quantity=Decimal("1"), unit_amount=Decimal("250.00"))],
            note=None,
            invoice_number="PP-TEST",
        ),
        request_id="req-create-1",
        session_id=session,
    )
    assert invoice["id"] == "INV2-SANDBOX-001"
    assert invoice["mock"] is False
    assert gateway.token_fetches == 1
    again, replay_traces = gateway.create_draft_invoice(
        CreateInvoiceRequest(
            recipient_email="buyer@example.com",
            recipient_name="Northwind Studio",
            currency_code="GBP",
            items=[LineItem(name="website work", quantity=Decimal("1"), unit_amount=Decimal("250.00"))],
            note=None,
            invoice_number="PP-TEST",
        ),
        request_id="req-create-1",
        session_id=session,
    )
    assert again["id"] == invoice["id"]
    assert replay_traces == []
    assert state["invoices"] == 1

    sent, _ = gateway.send_invoice("INV2-SANDBOX-001", request_id="req-send-1", session_id=session)
    assert sent["status"] == "SENT"
    assert sent["hosted_url"].startswith("https://www.sandbox.paypal.com/")
    assert gateway.token_fetches == 1

    listed, _ = gateway.list_invoices(session_id=session)
    assert listed["invoices"][0]["id"] == "INV2-SANDBOX-001"
    link, _ = gateway.create_payment_link(
        CreatePaymentLinkRequest(
            title="workshop", currency_code="GBP", unit_amount=Decimal("19.99"), reusable=True
        ),
        request_id="req-link-1",
        session_id=session,
    )
    assert link["link_kind"] == "paypal"
    assert "sandbox.paypal.com" in link["payment_link"]
    refund, _ = gateway.refund_capture(
        RefundCaptureRequest(
            capture_id="2GG279541U471931P",
            currency_code="GBP",
            amount=Decimal("19.99"),
            note="Refund from PayPilot",
        ),
        request_id="req-refund-1",
        session_id=session,
    )
    assert refund["id"] == "1JU08902781691411"
    assert refund["moves_money"] is True
    recorded, _ = gateway.record_invoice_refund(
        RecordRefundRequest(
            invoice_id="INV2-SANDBOX-001",
            currency_code="GBP",
            amount=Decimal("10.00"),
            method="OTHER",
            refund_date="2026-11-07",
        ),
        request_id="req-record-1",
        session_id=session,
    )
    assert recorded["moves_money"] is False
    assert "does not transfer money" in recorded["note"]
    blob = json.dumps({"traces": [trace.__dict__ for trace in traces], "invoice": invoice, "refund": refund})
    assert TOKEN not in blob
    assert SECRET not in blob
    assert CLIENT_ID not in blob


def test_token_refresh_is_bounded_and_errors_are_explicit(tmp_path):
    state = {"tokens": 0, "api": 0, "mode": "short"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth2/token":
            state["tokens"] += 1
            if state["mode"] == "deny":
                return httpx.Response(401, json={"error": "invalid_client", "error_description": "nope"})
            expires = 30 if state["mode"] == "short" else 3600
            return httpx.Response(200, json={"access_token": TOKEN, "expires_in": expires, "token_type": "Bearer"})
        state["api"] += 1
        if state["mode"] == "unauthorized":
            return httpx.Response(401, json={"name": "AUTHENTICATION_FAILURE", "message": "token", "debug_id": "dbg401"})
        if state["mode"] == "invalid":
            return httpx.Response(
                422,
                headers={"PayPal-Debug-Id": "dbg422"},
                json={
                    "name": "UNPROCESSABLE_ENTITY",
                    "message": "no",
                    "debug_id": "dbg422",
                    "details": [{"issue": "INVALID_AMOUNT", "description": "amount is invalid"}],
                    "access_token": TOKEN,
                },
            )
        return httpx.Response(200, json={"id": "INV2-SANDBOX-001", "status": "DRAFT"})

    app = create_app(_settings(tmp_path), paypal_transport=httpx.MockTransport(handler))
    gateway = app.state.book.gateway
    session = "ses_sandbox"
    gateway.db.insert_session(session, "sandbox", "mock")
    gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    assert state["tokens"] == 2  # expires_in 30 is inside the 60s refresh window

    state["mode"] = "long"
    state["tokens"] = 0
    gateway._token = ""
    gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    assert state["tokens"] == 1

    state["mode"] = "invalid"
    gateway._token = ""
    with pytest.raises(PayPalAPIError) as caught:
        gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    assert caught.value.status_code == 422
    assert "422" in caught.value.message
    assert "amount is invalid" in caught.value.message
    assert "dbg422" in caught.value.message
    assert TOKEN not in caught.value.message
    assert SECRET not in caught.value.message

    state["mode"] = "unauthorized"
    gateway._invalidate_token()
    with pytest.raises(PayPalAPIError) as caught:
        gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    assert caught.value.status_code == 401
    assert "401" in caught.value.message
    assert TOKEN not in caught.value.message

    state["mode"] = "deny"
    gateway._invalidate_token()
    gateway._consecutive_token_failures = 0
    before = state["tokens"]
    with pytest.raises(PayPalAPIError):
        gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    failed_fetches = state["tokens"] - before
    assert failed_fetches == 2
    before = state["tokens"]
    with pytest.raises(PayPalAPIError):
        gateway.get_invoice("INV2-SANDBOX-001", session_id=session)
    assert state["tokens"] == before
