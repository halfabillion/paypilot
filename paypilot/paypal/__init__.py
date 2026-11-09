"""PayPal gateway construction."""

from __future__ import annotations

import httpx

from paypilot.config import Settings
from paypilot.db import Database
from paypilot.models import Invoicer
from paypilot.paypal.mock import MockPayPal
from paypilot.paypal.sandbox import SandboxPayPal


def build_invoicer(settings: Settings) -> Invoicer:
    return Invoicer(
        email=settings.invoicer_email,
        given_name=settings.invoicer_given_name,
        surname=settings.invoicer_surname,
        business_name=settings.invoicer_business_name,
    )


def build_gateway(settings: Settings, db: Database, *, transport: httpx.BaseTransport | None = None):
    invoicer = build_invoicer(settings)
    if settings.paypal_mode == "sandbox":
        http = httpx.Client(
            base_url=settings.paypal_base_url,
            timeout=httpx.Timeout(20.0, connect=5.0),
            transport=transport,
        )
        return SandboxPayPal(settings, db, invoicer, http)
    return MockPayPal(db, invoicer)
