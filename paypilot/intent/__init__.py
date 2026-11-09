"""Intent provider construction. Mock is the default; the LLM adapter is optional."""

from __future__ import annotations

import httpx

from paypilot.config import Settings
from paypilot.intent.mock import MockIntent
from paypilot.intent.openai_compatible import OpenAICompatibleIntent


def build_intent(settings: Settings, *, transport: httpx.BaseTransport | None = None):
    if settings.intent_provider == "openai":
        http = httpx.Client(
            base_url=settings.openai_base_url.rstrip("/"),
            timeout=httpx.Timeout(30.0, connect=5.0),
            transport=transport,
        )
        return OpenAICompatibleIntent(
            http=http,
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            secrets=settings.secret_values(),
        )
    return MockIntent()
