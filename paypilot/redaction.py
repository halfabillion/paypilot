"""Strip credentials from anything that might be stored or returned."""

from __future__ import annotations

import re
from typing import Any

_SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "id_token",
    "client_secret",
    "client_id",
    "authorization",
    "password",
    "secret",
    "paypal_client_secret",
    "openai_api_key",
}
_BEARER = re.compile(r"Bearer\s+[A-Za-z0-9._\-+/=]+", re.IGNORECASE)
_BASIC = re.compile(r"Basic\s+[A-Za-z0-9+/=]+", re.IGNORECASE)
_SK = re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}\b")


def redact_text(text: str, secrets: list[str] | None = None) -> str:
    cleaned = _BEARER.sub("Bearer [redacted]", text)
    cleaned = _BASIC.sub("Basic [redacted]", cleaned)
    cleaned = _SK.sub("[redacted]", cleaned)
    for secret in secrets or []:
        if secret and len(secret) >= 8:
            cleaned = cleaned.replace(secret, "[redacted]")
    return cleaned


def redact_obj(value: Any, secrets: list[str] | None = None) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                redacted[str(key)] = "[redacted]"
            else:
                redacted[str(key)] = redact_obj(item, secrets)
        return redacted
    if isinstance(value, list):
        return [redact_obj(item, secrets) for item in value]
    if isinstance(value, str):
        return redact_text(value, secrets)
    return value
