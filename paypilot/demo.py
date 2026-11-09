"""Offline walkthrough of the demo script. This never calls PayPal.

The default intent provider is the deterministic parser. Pass
--use-configured-intent to use PAYPILOT_INTENT_PROVIDER from the environment
instead. PayPal mode stays MOCK either way.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

from fastapi.testclient import TestClient

from paypilot.config import Settings, load_settings
from paypilot.errors import ConfigError
from paypilot.disclosure import BUILD_DISCLOSURE
from paypilot.models import SEEDED_CAPTURE_ID, SEEDED_INVOICE_ID

CANARY_SECRET = "canary-secret-do-not-leak-9f3a"
CANARY_KEY = "canary-openai-do-not-leak-9f3a"

INVOICE_REQUEST = "Invoice buyer@example.com, Northwind Studio, £250 for website work"
SEND_REQUEST = "Send that invoice"
STATUS_REQUEST = "What is the status of that invoice?"
LIST_REQUEST = "List invoices"
LINK_REQUEST = "Give me a reusable £19.99 pay link for the workshop"
REFUND_REQUEST = f"Refund capture {SEEDED_CAPTURE_ID} for £19.99"
RECORD_REQUEST = f"Record an external refund of £10 on {SEEDED_INVOICE_ID}"
BAD_AMOUNT_REQUEST = "Invoice buyer@example.com £-5 for nothing"
REJECT_REQUEST = "Invoice buyer@example.com, Acme, £10 for a logo"


class DemoFailure(RuntimeError):
    pass


def build_demo_settings(database_path: str, *, use_configured_intent: bool) -> Settings:
    """MOCK PayPal settings. Intent comes from the environment only when asked."""
    if not use_configured_intent:
        return Settings(
            paypal_mode="mock",
            intent_provider="mock",
            paypal_client_secret=CANARY_SECRET,
            openai_api_key=CANARY_KEY,
            database_path=database_path,
            rate_limit_per_minute=0,
        )
    loaded = load_settings()
    return Settings(
        paypal_mode="mock",
        intent_provider=loaded.intent_provider,
        openai_base_url=loaded.openai_base_url,
        openai_api_key=loaded.openai_api_key,
        openai_model=loaded.openai_model,
        paypal_client_secret=CANARY_SECRET,
        database_path=database_path,
        rate_limit_per_minute=0,
    )


def run_demo(database_path: str, *, use_configured_intent: bool = False) -> str:
    settings = build_demo_settings(
        database_path, use_configured_intent=use_configured_intent
    )
    from paypilot.main import create_app

    client = TestClient(create_app(settings))
    lines: list[str] = []

    def say(text: str = "") -> None:
        lines.append(text)

    def check(condition: bool, detail: str) -> None:
        if not condition:
            raise DemoFailure(detail)

    health = client.get("/api/health")
    check(health.status_code == 200, "health failed")
    body = health.json()
    check(body["paypal_mode"] == "mock", "demo must stay in mock mode")
    check(body["live_payments_enabled"] is False, "live payments must stay off")
    check(body["sandbox_credentials_configured"] is False, "canary secret must not count as sandbox config")

    say("PayPilot offline demo")
    if settings.intent_provider == "mock":
        say("This walkthrough follows the demo-script beats. It does not call PayPal or a language model.")
    else:
        say(
            "This walkthrough follows the demo-script beats in MOCK PayPal mode. "
            f"Intent provider is {settings.intent_provider} ({settings.openai_model}) from the environment."
        )
    say("")
    say("[0:00] Mode banner")
    meta = client.get("/api/meta").json()
    say(meta["banner"])
    say(f"Intent provider: {meta['intent_label']}")
    check("MOCK" in meta["banner"], "banner must say MOCK")

    session = client.post("/api/sessions").json()
    session_id = session["session_id"]
    check(session["activity"] == [], "a new session must not pretend a PayPal call happened")

    seen_proposal_ids: set[str] = set()

    def pending(view: dict, action: str) -> dict:
        proposal = view.get("turn_proposal")
        if not proposal or proposal["action"] != action or proposal["status"] != "pending":
            raise DemoFailure(f"this step did not create a pending {action}, got {proposal}")
        if proposal["id"] in seen_proposal_ids:
            raise DemoFailure(f"step reused proposal {proposal['id']}")
        seen_proposal_ids.add(proposal["id"])
        if view.get("last_proposal") and view["last_proposal"]["id"] != proposal["id"]:
            raise DemoFailure("turn proposal disagrees with the newest stored proposal")
        return proposal

    say("")
    say("[0:15] Invoice proposal, then a separate confirm")
    view = _message(client, session_id, INVOICE_REQUEST)
    proposal = pending(view, "create_invoice")
    say(f"USER {INVOICE_REQUEST}")
    say(f"PROPOSAL {proposal['id']} {proposal['paypal_call']} pending")
    say(proposal["summary"])
    check(view["activity"] == [], "creating a proposal must not call PayPal")
    check("POST /v2/invoicing/invoices" == proposal["paypal_call"], "create call path")
    view = _confirm(client, session_id, proposal["id"])
    created = _result_message(view)
    say(f"ASSISTANT {created}")
    create_activity = view["activity"][-1]
    check(create_activity["method"] == "POST", "create method")
    check(create_activity["path"] == "/v2/invoicing/invoices", "create path")
    check(create_activity["status_code"] == 201, "create status")
    check("MOCK" in (create_activity["resource_id"] or ""), "mock invoice id")
    check("DRAFT" in created and "has not been paid" in created, "draft is not paid")
    check("not a PayPal sandbox invoice" in created, "mock id labeled")
    invoice_id = create_activity["resource_id"]

    say("")
    say("[0:45] Send is a second confirmation")
    view = _message(client, session_id, SEND_REQUEST)
    proposal = pending(view, "send_invoice")
    check(proposal["paypal_call"].endswith("/send"), "send path")
    calls_before_send = len(view["activity"])
    check(calls_before_send == 1, "send proposal must not add a call")
    say(f"USER {SEND_REQUEST}")
    say(proposal["summary"])
    view = _confirm(client, session_id, proposal["id"])
    sent = _result_message(view)
    say(f"ASSISTANT {sent}")
    send_row = next(row for row in view["activity"] if row["path"].endswith("/send"))
    check(send_row["status_code"] == 202, "send status")
    check("SENT" in sent and "unpaid" in sent, "sent invoice stays unpaid")
    check("mock.paypilot.invalid" in sent, "mock hosted link")
    check(invoice_id in sent, "send refers to the draft")

    say("")
    say("[1:05] Status comes from a GET")
    view = _message(client, session_id, STATUS_REQUEST)
    status_text = _result_message(view)
    say(f"USER {STATUS_REQUEST}")
    say(f"ASSISTANT {status_text}")
    get_row = next(
        row
        for row in view["activity"]
        if row["method"] == "GET" and row["path"] == f"/v2/invoicing/invoices/{invoice_id}"
    )
    check(get_row["status_code"] == 200, "get status")
    check(f"status is SENT" in status_text, "authoritative SENT")
    check("unpaid" in status_text.lower(), "plain unpaid")
    check("is PAID" not in status_text, "status must not call the sent invoice paid")

    say("")
    say("[1:25] Invoice list, then a payment link")
    say("Payment Links is implemented. A sandbox app must have that product enabled.")
    say("This offline run uses the mock gateway, so the link is a fixture.")
    view = _message(client, session_id, LIST_REQUEST)
    listed = _result_message(view)
    say(f"USER {LIST_REQUEST}")
    say(f"ASSISTANT {listed}")
    check(invoice_id in listed and "SENT" in listed, "list contains the sent invoice")
    check(SEEDED_INVOICE_ID in listed and "seeded fixture" in listed, "seeded row is labeled")
    check(any(row["path"].startswith("/v2/invoicing/invoices?") for row in view["activity"]), "list call")

    view = _message(client, session_id, LINK_REQUEST)
    proposal = pending(view, "create_payment_link")
    say(f"USER {LINK_REQUEST}")
    say(proposal["summary"])
    check("POST /v1/checkout/payment-resources" == proposal["paypal_call"], "link path")
    view = _confirm(client, session_id, proposal["id"])
    linked = _result_message(view)
    say(f"ASSISTANT {linked}")
    link_row = next(row for row in view["activity"] if row["path"] == "/v1/checkout/payment-resources")
    check(link_row["status_code"] == 201, "link status")
    check("MOCK" in (link_row["resource_id"] or ""), "mock link id")
    check("mock.paypilot.invalid" in linked, "mock link host")
    check("not a PayPal-hosted page" in linked, "link labeled")

    say("")
    say("[1:45] Refund a known mock capture")
    say(f"The capture {SEEDED_CAPTURE_ID} is a seeded fixture, not a sandbox capture.")
    view = _message(client, session_id, REFUND_REQUEST)
    proposal = pending(view, "refund_capture")
    say(f"USER {REFUND_REQUEST}")
    say(proposal["summary"])
    check(proposal["moves_money"] is True, "capture refund is the money-moving action")
    check(any("does not" in warning or "no money" in warning.lower() or "no money" in proposal["summary"].lower() for warning in proposal["warnings"]) or "no money moves" in proposal["summary"], "mock refund warning")
    view = _confirm(client, session_id, proposal["id"])
    refunded = _result_message(view)
    say(f"ASSISTANT {refunded}")
    refund_row = next(row for row in view["activity"] if "/refund" in row["path"] and "refunds" not in row["path"])
    check(refund_row["method"] == "POST", "refund method")
    check(SEEDED_CAPTURE_ID in refund_row["path"], "refund path uses the named capture")
    check("MOCK" in (refund_row["resource_id"] or ""), "mock refund id")
    check("no money moved at PayPal" in refunded, "mock refund disclaimer")

    say("")
    say("[2:00] Record an external refund. This does not transfer money.")
    view = _message(client, session_id, RECORD_REQUEST)
    proposal = pending(view, "record_invoice_refund")
    say(f"USER {RECORD_REQUEST}")
    say(proposal["summary"])
    check(proposal["moves_money"] is False, "recorded refund must not be marked as moving money")
    check("does not transfer money" in proposal["summary"], "record summary")
    view = _confirm(client, session_id, proposal["id"])
    recorded = _result_message(view)
    say(f"ASSISTANT {recorded}")
    record_row = next(row for row in view["activity"] if row["path"].endswith("/refunds"))
    check(record_row["moves_money"] is False, "record activity does not move money")
    check("does not transfer money" in recorded, "record result")
    check("PARTIALLY_REFUNDED" in recorded, "partial recorded refund")
    check("EXTR-MOCK-" in (record_row["resource_id"] or ""), "recorded id labeled mock")

    say("")
    say("[2:10] Invalid amount, rejected proposal, secret redaction")
    before = len(view["activity"])
    view = _message(client, session_id, BAD_AMOUNT_REQUEST)
    refused = _result_message(view)
    say(f"USER {BAD_AMOUNT_REQUEST}")
    say(f"ASSISTANT {refused}")
    check("PayPal was not called." in refused, "invalid amount says PayPal was not called")
    check(len(view["activity"]) == before, "invalid amount adds no PayPal call")
    check(view.get("turn_proposal") is None, "invalid amount must not create a proposal")

    view = _message(client, session_id, REJECT_REQUEST)
    proposal = pending(view, "create_invoice")
    calls_before_reject = len(view["activity"])
    view = _reject(client, session_id, proposal["id"])
    rejected = _result_message(view)
    say(f"USER {REJECT_REQUEST}")
    say(f"ASSISTANT {rejected}")
    check(rejected == "Rejected. PayPal was not called.", "reject copy")
    check(len(view["activity"]) == calls_before_reject, "reject adds no PayPal call")

    raw = json.dumps(view)
    check(CANARY_SECRET not in raw, "client secret leaked into the session")
    check(CANARY_KEY not in raw, "model key leaked into the session")
    check("Bearer " not in raw, "bearer token leaked into the session")
    check("client_secret" not in raw.lower(), "client_secret field leaked")
    say("Activity feed and session payload do not contain the canary secrets.")

    say("")
    say("[2:25] What actually ran")
    say(BUILD_DISCLOSURE)
    say("Sandbox only when you supply your own REST app credentials. This demo used none.")
    say("No real funds.")
    say("")
    say(
        "DEMO PASS "
        f"activity={len(view['activity'])} "
        f"invoice={invoice_id} "
        f"mode=mock"
    )
    transcript = "\n".join(lines)
    secret_values = [CANARY_SECRET, CANARY_KEY, settings.openai_api_key, settings.paypal_client_secret]
    for secret in secret_values:
        if secret and len(secret) >= 8 and secret in transcript:
            raise DemoFailure("a secret was printed")
    return transcript


def _message(client: TestClient, session_id: str, text: str) -> dict:
    response = client.post(f"/api/sessions/{session_id}/messages", json={"text": text})
    if response.status_code != 200:
        raise DemoFailure(f"message failed {response.status_code} {response.text}")
    return response.json()


def _confirm(client: TestClient, session_id: str, proposal_id: str) -> dict:
    response = client.post(f"/api/sessions/{session_id}/proposals/{proposal_id}/confirm")
    if response.status_code != 200:
        raise DemoFailure(f"confirm failed {response.status_code} {response.text}")
    return response.json()


def _reject(client: TestClient, session_id: str, proposal_id: str) -> dict:
    response = client.post(f"/api/sessions/{session_id}/proposals/{proposal_id}/reject")
    if response.status_code != 200:
        raise DemoFailure(f"reject failed {response.status_code} {response.text}")
    return response.json()


def _result_message(view: dict) -> str:
    for message in reversed(view["messages"]):
        if message["role"] == "assistant":
            return message["body"]
    raise DemoFailure("no assistant message")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the PayPilot MOCK demo script.")
    parser.add_argument(
        "--use-configured-intent",
        action="store_true",
        help=(
            "Use PAYPILOT_INTENT_PROVIDER, OPENAI_BASE_URL, OPENAI_MODEL, and "
            "OPENAI_API_KEY from the environment. PayPal stays MOCK."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    path = Path("/tmp") / "paypilot-demo.sqlite"
    if path.exists():
        path.unlink()
    try:
        transcript = run_demo(str(path), use_configured_intent=args.use_configured_intent)
    except (DemoFailure, ConfigError) as exc:
        message = exc.message if isinstance(exc, ConfigError) else str(exc)
        print(f"DEMO FAIL {message}", file=sys.stderr)
        return 1
    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
