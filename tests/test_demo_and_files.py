from pathlib import Path

from paypilot.demo import CANARY_KEY, CANARY_SECRET, build_demo_settings, parse_args, run_demo
from paypilot.intent.mock import MockIntent
from paypilot.models import IntentDraft


def test_demo_script_passes_offline(tmp_path):
    transcript = run_demo(str(tmp_path / "demo.sqlite"))
    assert "DEMO PASS" in transcript
    assert "MOCK mode" in transcript
    assert "does not transfer money" in transcript
    assert "PayPal was not called." in transcript
    assert CANARY_SECRET not in transcript
    assert CANARY_KEY not in transcript
    assert "not runtime AI or sandbox proof" in transcript


def test_demo_flag_uses_configured_intent_and_stays_on_mock(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYPILOT_PAYPAL_MODE", "mock")
    monkeypatch.setenv("PAYPILOT_INTENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert parse_args([]).use_configured_intent is False
    assert parse_args(["--use-configured-intent"]).use_configured_intent is True
    forced = build_demo_settings(str(tmp_path / "configured.sqlite"), use_configured_intent=True)
    assert forced.paypal_mode == "mock"
    assert forced.intent_provider == "openai"
    assert forced.openai_base_url == "http://127.0.0.1:11434/v1"
    assert forced.openai_model == "qwen2.5:3b"
    assert forced.openai_api_key == ""
    transcript = run_demo(str(tmp_path / "default.sqlite"))
    assert "Deterministic parser" in transcript


def test_configured_demo_passes_when_the_model_invents_terms(monkeypatch, tmp_path):
    monkeypatch.setenv("PAYPILOT_PAYPAL_MODE", "mock")
    monkeypatch.setenv("PAYPILOT_INTENT_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:11434/v1")
    monkeypatch.setenv("OPENAI_MODEL", "qwen2.5:3b")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    class SpuriousTerms:
        """Deterministic parse, plus the NET terms qwen invented in the live retest."""

        name = "openai"

        def __init__(self):
            self._inner = MockIntent()

        def parse(self, text, context):
            lowered = text.lower()
            # Live qwen misrouted the payment-link step to the previous list action.
            if "pay link" in lowered or "payment link" in lowered:
                return IntentDraft(action="list_invoices", assistant_message="", fields={})
            draft = self._inner.parse(text, context)
            if draft.action != "create_invoice":
                return draft
            fields = dict(draft.fields)
            lowered = text.lower()
            if any(phrase in lowered for phrase in ("due on receipt", "payable on receipt", "due now", "immediately")):
                fields["payment_term"] = {"term_type": "NET_30"}
            else:
                fields["payment_term"] = {"term_type": "NET_60"}
            return IntentDraft(
                action=draft.action,
                assistant_message=draft.assistant_message,
                missing=list(draft.missing),
                fields=fields,
            )

    monkeypatch.setattr("paypilot.main.build_intent", lambda settings, transport=None: SpuriousTerms())
    transcript = run_demo(str(tmp_path / "spurious.sqlite"), use_configured_intent=True)
    assert "DEMO PASS" in transcript
    assert "terms: default (not stated)" in transcript


def test_license_env_example_and_gitignore():
    root = Path(__file__).resolve().parents[1]
    license_text = (root / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text
    assert "Copyright (c) 2026 Ali Ghaedi" in license_text
    example = (root / ".env.example").read_text(encoding="utf-8")
    assert "PAYPAL_CLIENT_SECRET=" in example
    secret_line = next(line for line in example.splitlines() if line.startswith("PAYPAL_CLIENT_SECRET="))
    assert secret_line.split("=", 1)[1] == ""
    key_line = next(line for line in example.splitlines() if line.startswith("OPENAI_API_KEY="))
    assert key_line.split("=", 1)[1] == ""
    ignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore.splitlines()
    assert "data/" in ignore.splitlines()
