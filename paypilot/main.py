"""Local FastAPI app. The server binds to loopback when launched with `python -m paypilot`."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from paypilot import __version__
from paypilot.config import Settings, load_settings, validate_settings
from paypilot.db import Database
from paypilot.errors import PayPalAPIError, PayPilotError
from paypilot.intent import build_intent
from paypilot.paypal import build_gateway
from paypilot.redaction import redact_text
from paypilot.service import Bookkeeper

STATIC_DIR = Path(__file__).resolve().parent / "static"


class MessageIn(BaseModel):
    text: str


class ConfirmIn(BaseModel):
    recipient_name: str | None = None


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        if self.per_minute <= 0:
            return
        now = time.monotonic()
        with self._lock:
            window = [stamp for stamp in self._hits.get(key, []) if now - stamp < 60]
            if len(window) >= self.per_minute:
                from paypilot.errors import RateLimitError

                raise RateLimitError("Too many requests. Wait a minute and try again.")
            window.append(now)
            self._hits[key] = window


def create_app(
    settings: Settings | None = None,
    *,
    paypal_transport=None,
    intent_transport=None,
) -> FastAPI:
    settings = settings or load_settings()
    validate_settings(settings)
    db = Database(settings.database_path)
    gateway = build_gateway(settings, db, transport=paypal_transport)
    intent = build_intent(settings, transport=intent_transport)
    book = Bookkeeper(db, gateway, intent, settings)
    app = FastAPI(title="PayPilot", version=__version__)
    app.state.book = book
    app.state.settings = settings
    app.state.limiter = RateLimiter(settings.rate_limit_per_minute)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(PayPilotError)
    async def paypilot_error(_request: Request, exc: PayPilotError):
        message = redact_text(exc.message, settings.secret_values())
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": message}},
        )

    @app.exception_handler(RequestValidationError)
    async def bad_body(_request: Request, _exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "Request body is invalid."}},
        )

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "version": __version__,
            "paypal_mode": settings.paypal_mode,
            "paypal_base_url": "https://api-m.sandbox.paypal.com",
            "intent_provider": settings.intent_provider,
            "openai_configured": settings.intent_provider == "openai",
            "sandbox_credentials_configured": bool(
                settings.paypal_client_id and settings.paypal_client_secret
            ),
            "live_payments_enabled": False,
        }

    @app.get("/api/meta")
    def meta():
        return {"version": __version__, **book.meta()}

    @app.post("/api/sessions")
    def create_session():
        return book.create_session()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str):
        return book.view(session_id)

    @app.post("/api/sessions/{session_id}/messages")
    def post_message(session_id: str, body: MessageIn):
        app.state.limiter.check(session_id)
        return _call(book, session_id, lambda: book.post_message(session_id, body.text))

    @app.post("/api/sessions/{session_id}/proposals/{proposal_id}/confirm")
    def confirm(session_id: str, proposal_id: str, body: ConfirmIn | None = Body(default=None)):
        app.state.limiter.check(session_id)
        name = None if body is None else body.recipient_name
        return _call(book, session_id, lambda: book.confirm(session_id, proposal_id, name))

    @app.post("/api/sessions/{session_id}/proposals/{proposal_id}/reject")
    def reject(session_id: str, proposal_id: str):
        app.state.limiter.check(session_id)
        return book.reject(session_id, proposal_id)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    return app


def _call(book: Bookkeeper, session_id: str, action):
    try:
        return action()
    except PayPalAPIError as exc:
        message = redact_text(exc.message, book.settings.secret_values())
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {"code": exc.code, "message": message},
                "session": book.view(session_id),
            },
        )


def serve() -> None:
    import uvicorn

    settings = load_settings()
    uvicorn.run(
        "paypilot.main:create_app",
        factory=True,
        host="127.0.0.1",
        port=settings.port,
        reload=False,
    )
