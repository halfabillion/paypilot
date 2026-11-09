"""Process configuration. Secrets stay in the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from paypilot.errors import ConfigError

SANDBOX_HOST = "api-m.sandbox.paypal.com"
SANDBOX_BASE_URL = f"https://{SANDBOX_HOST}"
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def load_dotenv(path: Path | None = None) -> None:
    """Fill missing environment variables from a local .env file.

    Existing variables win, so a shell export is never overwritten. Values are
    not logged.
    """
    env_path = path or Path(".env")
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    paypal_mode: str = "mock"
    intent_provider: str = "mock"
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_base_url: str = SANDBOX_BASE_URL
    invoicer_email: str = ""
    invoicer_given_name: str = "PayPilot"
    invoicer_surname: str = "Merchant"
    invoicer_business_name: str = "PayPilot Demo"
    openai_base_url: str = ""
    openai_api_key: str = ""
    openai_model: str = ""
    database_path: str = "data/paypilot.sqlite"
    port: int = 8765
    rate_limit_per_minute: int = 60
    return_url: str = ""

    def secret_values(self) -> list[str]:
        values = [self.paypal_client_secret, self.openai_api_key, self.paypal_client_id]
        return [value for value in values if value and len(value) >= 8]


def load_settings() -> Settings:
    load_dotenv()
    mode = os.environ.get("PAYPILOT_PAYPAL_MODE", "mock").strip().lower() or "mock"
    provider = os.environ.get("PAYPILOT_INTENT_PROVIDER", "mock").strip().lower() or "mock"
    port = _port(os.environ.get("PAYPILOT_PORT", "8765"))
    limit = _limit(os.environ.get("PAYPILOT_RATE_LIMIT_PER_MINUTE", "60"))
    settings = Settings(
        paypal_mode=mode,
        intent_provider=provider,
        paypal_client_id=os.environ.get("PAYPAL_CLIENT_ID", "").strip(),
        paypal_client_secret=os.environ.get("PAYPAL_CLIENT_SECRET", "").strip(),
        paypal_base_url=os.environ.get("PAYPAL_BASE_URL", SANDBOX_BASE_URL).strip()
        or SANDBOX_BASE_URL,
        invoicer_email=os.environ.get("PAYPAL_INVOICER_EMAIL", "").strip(),
        invoicer_given_name=os.environ.get("PAYPAL_INVOICER_GIVEN_NAME", "PayPilot").strip()
        or "PayPilot",
        invoicer_surname=os.environ.get("PAYPAL_INVOICER_SURNAME", "Merchant").strip()
        or "Merchant",
        invoicer_business_name=os.environ.get(
            "PAYPAL_INVOICER_BUSINESS_NAME", "PayPilot Demo"
        ).strip()
        or "PayPilot Demo",
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "").strip(),
        database_path=os.environ.get("PAYPILOT_DB", "data/paypilot.sqlite").strip()
        or "data/paypilot.sqlite",
        port=port,
        rate_limit_per_minute=limit,
        return_url=os.environ.get("PAYPILOT_RETURN_URL", "").strip(),
    )
    validate_settings(settings)
    return settings


def validate_settings(settings: Settings) -> None:
    if settings.paypal_mode not in {"mock", "sandbox"}:
        raise ConfigError("PAYPILOT_PAYPAL_MODE must be mock or sandbox.")
    if settings.intent_provider not in {"mock", "openai"}:
        raise ConfigError("PAYPILOT_INTENT_PROVIDER must be mock or openai.")
    validate_paypal_base_url(settings.paypal_base_url)
    if settings.paypal_mode == "sandbox":
        if not settings.paypal_client_id or not settings.paypal_client_secret:
            raise ConfigError(
                "SANDBOX mode requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET. "
                "PayPilot will not fall back to mock mode."
            )
    if settings.intent_provider == "openai":
        _validate_openai(settings)
    if settings.return_url:
        parsed = urlparse(settings.return_url)
        if parsed.scheme not in {"https", "http"} or not parsed.netloc:
            raise ConfigError("PAYPILOT_RETURN_URL must be an http(s) URL.")


def validate_paypal_base_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.username or parsed.password:
        raise ConfigError("PAYPAL_BASE_URL must not include credentials.")
    if parsed.scheme != "https" or parsed.hostname != SANDBOX_HOST:
        raise ConfigError(
            "PAYPAL_BASE_URL must be https://api-m.sandbox.paypal.com. "
            "Live PayPal hosts are refused."
        )
    if parsed.path not in {"", "/"} or parsed.query or parsed.params or parsed.fragment:
        raise ConfigError("PAYPAL_BASE_URL must not include a path or query.")
    return SANDBOX_BASE_URL


def _validate_openai(settings: Settings) -> None:
    if not settings.openai_model:
        raise ConfigError("OPENAI_MODEL is required when PAYPILOT_INTENT_PROVIDER=openai.")
    if not settings.openai_base_url:
        raise ConfigError("OPENAI_BASE_URL is required when PAYPILOT_INTENT_PROVIDER=openai.")
    parsed = urlparse(settings.openai_base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigError("OPENAI_BASE_URL must be an http(s) URL.")
    if parsed.hostname not in _LOCAL_HOSTS and not settings.openai_api_key:
        raise ConfigError(
            "OPENAI_API_KEY is required when OPENAI_BASE_URL is not on localhost."
        )


def _port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise ConfigError("PAYPILOT_PORT must be an integer.") from exc
    if port < 1 or port > 65535:
        raise ConfigError("PAYPILOT_PORT must be between 1 and 65535.")
    return port


def _limit(raw: str) -> int:
    try:
        limit = int(raw)
    except ValueError as exc:
        raise ConfigError("PAYPILOT_RATE_LIMIT_PER_MINUTE must be an integer.") from exc
    if limit < 0:
        raise ConfigError("PAYPILOT_RATE_LIMIT_PER_MINUTE cannot be negative.")
    return limit
