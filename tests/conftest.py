import warnings

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")

import pytest
from fastapi.testclient import TestClient

from paypilot.config import Settings
from paypilot.main import create_app


@pytest.fixture()
def settings(tmp_path):
    return Settings(
        paypal_mode="mock",
        intent_provider="mock",
        database_path=str(tmp_path / "paypilot.sqlite"),
        rate_limit_per_minute=0,
        paypal_client_id="test-sandbox-client-id",
        paypal_client_secret="test-sandbox-secret-value",
        openai_api_key="test-openai-key-value",
    )


@pytest.fixture()
def client(settings):
    return TestClient(create_app(settings))


def new_session(client) -> str:
    response = client.post("/api/sessions")
    assert response.status_code == 200
    return response.json()["session_id"]


def say(client, session_id: str, text: str):
    response = client.post(f"/api/sessions/{session_id}/messages", json={"text": text})
    return response


def confirm(client, session_id: str, proposal_id: str, body: dict | None = None):
    if body is None:
        return client.post(f"/api/sessions/{session_id}/proposals/{proposal_id}/confirm")
    return client.post(
        f"/api/sessions/{session_id}/proposals/{proposal_id}/confirm",
        json=body,
    )
