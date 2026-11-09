import json

import httpx
import pytest

from paypilot.config import Settings
from paypilot.errors import ConfigError
from paypilot.main import create_app


def test_remote_model_requires_a_key_and_local_does_not(tmp_path):
    with pytest.raises(ConfigError):
        create_app(
            Settings(
                intent_provider="openai",
                openai_base_url="https://api.openai.com/v1",
                openai_model="gpt-4o-mini",
                database_path=str(tmp_path / "a.sqlite"),
            )
        )
    with pytest.raises(ConfigError):
        create_app(
            Settings(
                intent_provider="openai",
                openai_base_url="http://127.0.0.1:11434/v1",
                openai_model="",
                database_path=str(tmp_path / "b.sqlite"),
            )
        )


def test_openai_adapter_parses_a_fixture_and_redacts_the_key(tmp_path):
    key = "test-openai-key-value"
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content)
        if seen.get("mode") == "clarify":
            content = json.dumps(
                {
                    "action": "clarify",
                    "assistant_message": f"The key is {key}",
                    "missing": ["amount"],
                    "fields": {},
                }
            )
        else:
            content = json.dumps(
                {
                    "action": "create_invoice",
                    "assistant_message": "I already created INV2-FAKE and it is paid.",
                    "missing": [],
                    "fields": {
                        "recipient_email": "buyer@example.com",
                        "recipient_name": "Northwind Studio",
                        "currency_code": "GBP",
                        "note": None,
                        "items": [
                            {"name": "website work", "quantity": "1", "unit_amount": "250.00"}
                        ],
                    },
                }
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": content}}]},
        )

    app = create_app(
        Settings(
            intent_provider="openai",
            openai_base_url="http://127.0.0.1:11434/v1",
            openai_model="fixture-model",
            openai_api_key=key,
            database_path=str(tmp_path / "openai.sqlite"),
            rate_limit_per_minute=0,
        ),
        intent_transport=httpx.MockTransport(handler),
    )
    client_session = app.state.book.create_session()
    view = app.state.book.post_message(
        client_session["session_id"],
        "Invoice buyer@example.com, Northwind Studio, £250 for website work",
    )
    proposal = view["last_proposal"]
    assert proposal["action"] == "create_invoice"
    assert proposal["status"] == "pending"
    assert view["activity"] == []
    assert "paid" not in proposal["summary"].lower()
    assert seen["auth"] == f"Bearer {key}"
    assert seen["body"]["temperature"] == 0
    assert key not in json.dumps(view)

    seen["mode"] = "clarify"
    clarified = app.state.book.post_message(client_session["session_id"], "hello there")
    assert key not in json.dumps(clarified)
    assert "[redacted]" in clarified["messages"][-1]["body"]
