"""Error types shared by the API, the intent providers, and the PayPal gateway."""


class PayPilotError(Exception):
    code = "error"
    http_status = 400

    def __init__(self, message: str, *, code: str | None = None, http_status: int | None = None):
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if http_status is not None:
            self.http_status = http_status


class ValidationError(PayPilotError):
    code = "validation_error"
    http_status = 422


class NotFoundError(PayPilotError):
    code = "not_found"
    http_status = 404


class ConflictError(PayPilotError):
    code = "conflict"
    http_status = 409


class ConfigError(PayPilotError):
    code = "config_error"
    http_status = 500


class IntentError(PayPilotError):
    code = "intent_error"
    http_status = 502


class RateLimitError(PayPilotError):
    code = "rate_limited"
    http_status = 429


class PayPalAPIError(PayPilotError):
    """A PayPal HTTP failure, or the mock gateway simulating one.

    `traces` holds the redacted call records the service should still audit.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        debug_id: str | None = None,
        traces: list | None = None,
    ):
        http = status_code if status_code in {401, 403, 404, 422} else 502
        super().__init__(message, code="paypal_error", http_status=http)
        self.status_code = status_code
        self.debug_id = debug_id
        self.traces = traces or []
