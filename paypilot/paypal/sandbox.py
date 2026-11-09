"""PayPal sandbox REST client. Live production hosts are refused by config."""

from __future__ import annotations

import logging
import threading
import time
from datetime import date
from decimal import Decimal
from urllib.parse import quote, urlparse

import httpx

from paypilot.config import SANDBOX_HOST, Settings
from paypilot.db import Database
from paypilot.errors import ConfigError, PayPalAPIError
from paypilot.models import (
    CallTrace,
    CreateInvoiceRequest,
    CreatePaymentLinkRequest,
    Invoicer,
    RecordRefundRequest,
    RefundCaptureRequest,
)
from paypilot.money import display_money, format_money
from paypilot.paypal.payloads import (
    capture_refund_body,
    invoice_create_body,
    invoice_send_body,
    payment_link_body,
    record_refund_body,
)
from paypilot.redaction import redact_obj, redact_text
from paypilot.status import plain_status

logger = logging.getLogger("paypilot.paypal")


class SandboxPayPal:
    """OAuth client-credentials client for https://api-m.sandbox.paypal.com."""

    mode = "sandbox"

    def __init__(self, settings: Settings, db: Database, invoicer: Invoicer, http: httpx.Client):
        if settings.paypal_mode != "sandbox":
            raise ConfigError("SandboxPayPal is only constructed in SANDBOX mode.")
        if not settings.paypal_client_id or not settings.paypal_client_secret:
            raise ConfigError(
                "SANDBOX mode requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET. "
                "PayPilot will not fall back to mock mode."
            )
        base = str(http.base_url).rstrip("/")
        if urlparse(base).hostname != SANDBOX_HOST or urlparse(base).scheme != "https":
            raise ConfigError(
                "The PayPal client is pinned to https://api-m.sandbox.paypal.com."
            )
        self.settings = settings
        self.db = db
        self.invoicer = invoicer
        self.http = http
        self.calls: list[str] = []
        self._lock = threading.Lock()
        self._token = ""
        self._expires_at = 0.0
        self._consecutive_token_failures = 0
        self.token_fetches = 0

    def seed(self, session_id: str) -> None:
        """Sandbox has no local fixture invoices. The merchant account is the source of truth."""
        return None

    def create_draft_invoice(
        self, request: CreateInvoiceRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("create_draft_invoice")
        cached = self._cached(request_id)
        if cached:
            return cached, []
        body = invoice_create_body(
            request,
            self.invoicer,
            invoice_date=date.today().isoformat(),
            fallback_email=None,
        )
        response, trace = self._request(
            "POST",
            "/v2/invoicing/invoices",
            json_body=body,
            request_id=request_id,
        )
        payload = _json(response)
        invoice_id = str(payload.get("id") or _id_from_location(response) or "")
        if not invoice_id:
            raise PayPalAPIError(
                "PayPal created a response without an invoice id.",
                status_code=502,
                traces=[trace],
            )
        trace.resource_id = invoice_id
        trace.summary = f"POST /v2/invoicing/invoices → {response.status_code} {invoice_id}"
        public = self._normalize_invoice(payload, fallback_id=invoice_id, request=request)
        self.db.put_idempotent(request_id, public)
        self.db.remember_resource(session_id, "invoice", public["id"])
        return public, [trace]

    def send_invoice(
        self, invoice_id: str, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("send_invoice")
        cached = self._cached(request_id)
        if cached:
            return cached, []
        path = f"/v2/invoicing/invoices/{quote(invoice_id, safe='')}/send"
        response, send_trace = self._request(
            "POST",
            path,
            json_body=invoice_send_body(),
            request_id=request_id,
        )
        send_trace.resource_id = invoice_id
        send_trace.summary = f"POST {path} → {response.status_code}"
        public, get_trace = self._get(invoice_id)
        self.db.put_idempotent(request_id, public)
        self.db.remember_resource(session_id, "invoice", public["id"])
        return public, [send_trace, get_trace]

    def get_invoice(self, invoice_id: str, *, session_id: str) -> tuple[dict, list[CallTrace]]:
        self.calls.append("get_invoice")
        public, trace = self._get(invoice_id)
        self.db.remember_resource(session_id, "invoice", public["id"])
        return public, [trace]

    def list_invoices(self, *, session_id: str) -> tuple[dict, list[CallTrace]]:
        self.calls.append("list_invoices")
        known = self.db.resources_for(session_id, "invoice")
        response, trace = self._request(
            "GET",
            "/v2/invoicing/invoices",
            params={"page": 1, "page_size": 20, "total_required": "true"},
        )
        traces = [trace]
        invoices: list[dict] = []
        # List proves the endpoint. Each known id is then read with GET so the
        # status on screen is the invoice resource, not a stale list row.
        for invoice_id in known:
            public, extra = self._get(invoice_id)
            invoices.append(public)
            traces.append(extra)
        trace.summary = f"GET /v2/invoicing/invoices → {response.status_code} {len(invoices)} in this session"
        return {"invoices": invoices}, traces

    def create_payment_link(
        self, request: CreatePaymentLinkRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("create_payment_link")
        cached = self._cached(request_id)
        if cached:
            return cached, []
        body = payment_link_body(request, return_url=self.settings.return_url)
        response, trace = self._request(
            "POST",
            "/v1/checkout/payment-resources",
            json_body=body,
            request_id=request_id,
        )
        payload = _json(response)
        link_id = str(payload.get("id") or "")
        href = _link(payload, "payment_link") or str(payload.get("payment_link") or "")
        if not link_id or not href:
            raise PayPalAPIError(
                "PayPal did not return a payment link id and payment_link URL.",
                status_code=502,
                traces=[trace],
            )
        result = {
            "id": link_id,
            "status": str(payload.get("status") or "ACTIVE"),
            "payment_link": href,
            "link_kind": "paypal",
            "title": request.title,
            "currency_code": request.currency_code,
            "total": format_money(request.unit_amount),
            "total_display": display_money(request.currency_code, request.unit_amount),
            "reusable": request.reusable,
            "mock": False,
        }
        trace.resource_id = link_id
        trace.summary = f"POST /v1/checkout/payment-resources → {response.status_code} {link_id}"
        self.db.put_idempotent(request_id, result)
        self.db.remember_resource(session_id, "payment_link", link_id)
        return result, [trace]

    def refund_capture(
        self, request: RefundCaptureRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("refund_capture")
        cached = self._cached(request_id)
        if cached:
            return cached, []
        path = f"/v2/payments/captures/{quote(request.capture_id, safe='')}/refund"
        response, trace = self._request(
            "POST",
            path,
            json_body=capture_refund_body(request),
            request_id=request_id,
            moves_money=True,
        )
        payload = _json(response)
        refund_id = str(payload.get("id") or "")
        if not refund_id:
            raise PayPalAPIError(
                "PayPal did not return a refund id.",
                status_code=502,
                traces=[trace],
            )
        result = {
            "id": refund_id,
            "status": str(payload.get("status") or ""),
            "capture_id": request.capture_id,
            "currency_code": request.currency_code,
            "total": format_money(request.amount),
            "total_display": display_money(request.currency_code, request.amount),
            "moves_money": True,
            "mock": False,
            "note": "Sandbox capture refund. This is a test-balance refund, not a live-funds refund.",
        }
        trace.resource_id = refund_id
        trace.summary = f"POST {path} → {response.status_code} {refund_id} {result['status']}"
        self.db.put_idempotent(request_id, result)
        self.db.remember_resource(session_id, "refund", refund_id)
        return result, [trace]

    def record_invoice_refund(
        self, request: RecordRefundRequest, *, request_id: str, session_id: str
    ) -> tuple[dict, list[CallTrace]]:
        self.calls.append("record_invoice_refund")
        cached = self._cached(request_id)
        if cached:
            return cached, []
        path = f"/v2/invoicing/invoices/{quote(request.invoice_id, safe='')}/refunds"
        response, trace = self._request(
            "POST",
            path,
            json_body=record_refund_body(request),
            request_id=request_id,
            moves_money=False,
        )
        payload = _json(response)
        refund_id = str(payload.get("refund_id") or payload.get("id") or "")
        public, get_trace = self._get(request.invoice_id)
        result = {
            "id": refund_id or "unspecified",
            "status": "RECORDED",
            "invoice_id": request.invoice_id,
            "invoice_status": public["status"],
            "currency_code": request.currency_code,
            "total": format_money(request.amount),
            "total_display": display_money(request.currency_code, request.amount),
            "method": request.method,
            "moves_money": False,
            "mock": False,
            "note": "Recorded on the invoice. Recording an external refund does not transfer money.",
        }
        trace.resource_id = result["id"]
        trace.summary = f"POST {path} → {response.status_code} {result['id']} recorded"
        self.db.put_idempotent(request_id, result)
        return result, [trace, get_trace]

    def _get(self, invoice_id: str) -> tuple[dict, CallTrace]:
        path = f"/v2/invoicing/invoices/{quote(invoice_id, safe='')}"
        response, trace = self._request("GET", path)
        payload = _json(response)
        public = self._normalize_invoice(payload, fallback_id=invoice_id)
        trace.resource_id = public["id"]
        trace.summary = f"GET {path} → {response.status_code} {public['status']}"
        return public, trace

    def _cached(self, request_id: str) -> dict | None:
        return self.db.get_idempotent(request_id)

    def _normalize_invoice(
        self,
        payload: dict,
        *,
        fallback_id: str,
        request: CreateInvoiceRequest | None = None,
    ) -> dict:
        invoice_id = str(payload.get("id") or fallback_id)
        status = str(payload.get("status") or "")
        due = payload.get("due_amount") if isinstance(payload.get("due_amount"), dict) else {}
        currency = str(due.get("currency_code") or (request.currency_code if request else ""))
        total = str(due.get("value") or (self._request_total(request) if request else ""))
        recipient = _recipient(payload)
        items = _item_summary(payload)
        if request and not recipient["email"]:
            recipient = {
                "email": request.recipient_email,
                "name": request.recipient_name,
            }
        if request and not items:
            items = ", ".join(item.name for item in request.items)
        if request and not currency:
            currency = request.currency_code
        if request and not total:
            total = self._request_total(request)
        hosted = _recipient_view(payload)
        return {
            "id": invoice_id,
            "status": status,
            "status_plain": plain_status(status),
            "recipient_email": recipient["email"],
            "recipient_name": recipient["name"],
            "currency_code": currency,
            "total": total,
            "total_display": display_money(currency, total) if currency and total else total,
            "refunded_total": "",
            "item_summary": items,
            "hosted_url": hosted,
            "hosted_url_kind": "paypal" if hosted else None,
            "seeded": False,
            "mock": False,
            "invoice_number": str((payload.get("detail") or {}).get("invoice_number") or ""),
        }

    @staticmethod
    def _request_total(request: CreateInvoiceRequest | None) -> str:
        if request is None:
            return ""
        total = sum((item.quantity * item.unit_amount for item in request.items), Decimal("0"))
        return format_money(total)

    def _access_token(self) -> str:
        with self._lock:
            now = time.monotonic()
            if self._token and now < self._expires_at - 60:
                return self._token
            if self._consecutive_token_failures >= 2:
                raise PayPalAPIError(
                    "PayPal token refresh stopped after repeated failures. "
                    "Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET.",
                    status_code=401,
                )
            last_error: PayPalAPIError | None = None
            for _ in range(2):
                try:
                    token, expires_in = self._fetch_token()
                except PayPalAPIError as exc:
                    self._consecutive_token_failures += 1
                    last_error = exc
                    self._token = ""
                    continue
                self._token = token
                self._expires_at = time.monotonic() + expires_in
                self._consecutive_token_failures = 0
                return token
            assert last_error is not None
            raise last_error

    def _fetch_token(self) -> tuple[str, int]:
        self.token_fetches += 1
        try:
            response = self.http.post(
                "/v1/oauth2/token",
                data={"grant_type": "client_credentials"},
                auth=(self.settings.paypal_client_id, self.settings.paypal_client_secret),
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise PayPalAPIError("PayPal token request timed out.", status_code=504) from exc
        except httpx.HTTPError as exc:
            raise PayPalAPIError("PayPal token request failed before a response.", status_code=502) from exc
        if response.status_code != 200:
            raise self._error(response, moves_money=False)
        payload = _json(response)
        token = str(payload.get("access_token") or "")
        expires_in = int(payload.get("expires_in") or 0)
        if not token or expires_in <= 0:
            raise PayPalAPIError(
                "PayPal token response did not include a usable access token.",
                status_code=502,
            )
        return token, expires_in

    def _invalidate_token(self) -> None:
        with self._lock:
            self._token = ""
            self._expires_at = 0.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        request_id: str | None = None,
        moves_money: bool = False,
        retry_auth: bool = True,
    ) -> tuple[httpx.Response, CallTrace]:
        token = self._access_token()
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if request_id:
            headers["PayPal-Request-Id"] = request_id
        try:
            response = self.http.request(method, path, json=json_body, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise PayPalAPIError(
                f"PayPal {method} {path} timed out. The call was not retried beyond the client timeout.",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise PayPalAPIError(
                f"PayPal {method} {path} failed before a response.",
                status_code=502,
            ) from exc
        logger.info("paypal %s %s -> %s", method, path, response.status_code)
        if response.status_code == 401 and retry_auth:
            self._invalidate_token()
            return self._request(
                method,
                path,
                json_body=json_body,
                params=params,
                request_id=request_id,
                moves_money=moves_money,
                retry_auth=False,
            )
        trace = self._trace(method, path, response, json_body, moves_money=moves_money)
        if response.status_code >= 400:
            raise self._error(response, moves_money=moves_money, trace=trace)
        return response, trace

    def _trace(
        self,
        method: str,
        path: str,
        response: httpx.Response,
        request_body: dict | None,
        *,
        moves_money: bool,
    ) -> CallTrace:
        secrets = self.settings.secret_values()
        response_body = _json_or_empty(response)
        debug_id = response.headers.get("PayPal-Debug-Id") or _debug_id(response_body)
        return CallTrace(
            method=method,
            path=path,
            status_code=response.status_code,
            summary=f"{method} {path} → {response.status_code}",
            request_body=redact_obj(request_body, secrets) if request_body is not None else None,
            response_body=redact_obj(response_body, secrets),
            debug_id=debug_id,
            moves_money=moves_money,
            mock=False,
        )

    def _error(
        self,
        response: httpx.Response,
        *,
        moves_money: bool,
        trace: CallTrace | None = None,
    ) -> PayPalAPIError:
        body = _json_or_empty(response)
        debug_id = None if trace is None else trace.debug_id
        if debug_id is None:
            debug_id = response.headers.get("PayPal-Debug-Id") or _debug_id(body)
        detail = _safe_detail(body, self.settings.secret_values())
        if response.status_code == 401:
            message = (
                "PayPal returned 401. The sandbox access token was rejected. "
                "Check PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET."
            )
        elif response.status_code == 403:
            message = (
                "PayPal returned 403. This REST app may not have the required product enabled "
                "(Invoicing, Payment Links, or refunds)."
            )
            if detail:
                message = f"{message} {detail}"
        elif response.status_code == 404:
            message = "PayPal returned 404. The resource was not found."
            if detail:
                message = f"{message} {detail}"
        elif response.status_code == 422:
            message = "PayPal returned 422. The request was rejected."
            if detail:
                message = f"{message} {detail}"
        else:
            message = f"PayPal returned {response.status_code}."
            if detail:
                message = f"{message} {detail}"
        if debug_id:
            message = f"{message} Debug id: {debug_id}."
        message = redact_text(message, self.settings.secret_values())
        traces = [trace] if trace is not None else []
        return PayPalAPIError(message, status_code=response.status_code, debug_id=debug_id, traces=traces)


def _json(response: httpx.Response) -> dict:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError as exc:
        raise PayPalAPIError("PayPal returned a non-JSON body.", status_code=502) from exc
    if not isinstance(payload, dict):
        raise PayPalAPIError("PayPal returned an unexpected JSON shape.", status_code=502)
    return payload


def _json_or_empty(response: httpx.Response) -> dict:
    if not response.content:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {"body": "[unreadable]"}
    return payload if isinstance(payload, dict) else {"body": "[unexpected]"}


def _debug_id(body: dict) -> str | None:
    value = body.get("debug_id")
    return str(value) if value else None


def _safe_detail(body: dict, secrets: list[str]) -> str:
    parts: list[str] = []
    details = body.get("details")
    if isinstance(details, list):
        for item in details[:3]:
            if isinstance(item, dict):
                issue = str(item.get("issue") or "")
                description = str(item.get("description") or "")
                piece = " ".join(bit for bit in (issue, description) if bit)
                if piece:
                    parts.append(piece)
    if not parts:
        description = body.get("error_description") or body.get("message")
        if isinstance(description, str) and description:
            parts.append(description)
    return redact_text(" ".join(parts), secrets)[:500]


def _id_from_location(response: httpx.Response) -> str:
    location = response.headers.get("Location") or ""
    return location.rstrip("/").split("/")[-1] if location else ""


def _link(payload: dict, rel: str) -> str:
    links = payload.get("links")
    if not isinstance(links, list):
        return ""
    for link in links:
        if isinstance(link, dict) and link.get("rel") == rel and link.get("href"):
            return str(link["href"])
    return ""


def _recipient_view(payload: dict) -> str | None:
    detail = payload.get("detail")
    if isinstance(detail, dict):
        metadata = detail.get("metadata")
        if isinstance(metadata, dict) and metadata.get("recipient_view_url"):
            return str(metadata["recipient_view_url"])
    href = _link(payload, "payer-view") or _link(payload, "payment_link")
    return href or None


def _recipient(payload: dict) -> dict[str, str]:
    recipients = payload.get("primary_recipients")
    if not isinstance(recipients, list) or not recipients:
        return {"email": "", "name": ""}
    billing = recipients[0].get("billing_info") if isinstance(recipients[0], dict) else None
    if not isinstance(billing, dict):
        return {"email": "", "name": ""}
    name = billing.get("name") if isinstance(billing.get("name"), dict) else {}
    full = " ".join(
        part for part in (str(name.get("given_name") or ""), str(name.get("surname") or "")) if part
    ).strip()
    return {"email": str(billing.get("email_address") or ""), "name": full}


def _item_summary(payload: dict) -> str:
    items = payload.get("items")
    if not isinstance(items, list):
        return ""
    names = [str(item.get("name")) for item in items if isinstance(item, dict) and item.get("name")]
    return ", ".join(names)
