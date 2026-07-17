"""Unit tests for llm-gateway, with the real Claude call *mocked out*.

Core idea: a unit test must not actually hit the network or spend API money.
So we replace the real Claude client with a "fake client" that returns a
canned result immediately. That makes the tests fast, stable, and free, and
lets us craft exact scenarios (success / failure).

Run:  uv run pytest -v
"""

from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main as gw
from main import app, get_llm

VALID_KEY = gw.GATEWAY_API_KEY   # a valid gateway key for the tests


# ============================================================
# Build a "fake Claude client"
# ============================================================

def _fake_message(text: str) -> SimpleNamespace:
    """Fake a Claude response object that looks like a real message (has the fields our code uses)."""
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],   # content is a list of blocks
        model="claude-opus-4-8",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )


@pytest.fixture
def client_with_fake_llm() -> Iterator[TestClient]:
    """fixture: build a TestClient and swap the real client for a fake one.

    app.dependency_overrides is FastAPI's official testing mechanism:
    override the get_llm dependency so the endpoint gets our fake client
    instead of a real AsyncAnthropic.
    """
    fake_llm = SimpleNamespace(messages=SimpleNamespace())
    # The fake messages.create is an AsyncMock (a stand-in for an async function); returns a fake message by default
    fake_llm.messages.create = AsyncMock(return_value=_fake_message("Hello, I am a fake reply"))

    app.dependency_overrides[get_llm] = lambda: fake_llm   # key step: inject the fake client
    with TestClient(app) as client:                        # TestClient runs the lifespan
        client.fake_llm = fake_llm                          # attach it so tests can assert on the call
        yield client
    app.dependency_overrides.clear()                       # clean up to avoid polluting other tests


# ============================================================
# Test cases (covers: success, auth failure, validation failure, upstream error, health check)
# ============================================================

def test_healthz(client_with_fake_llm: TestClient) -> None:
    """Health check should return 200 + ok."""
    resp = client_with_fake_llm.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_complete_success(client_with_fake_llm: TestClient) -> None:
    """Happy path: correct key + valid body -> 200, returns the fake client's canned text."""
    resp = client_with_fake_llm.post(
        "/v1/complete",
        headers={"X-API-Key": VALID_KEY},
        json={"prompt": "Tell me a joke", "max_tokens": 50},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["text"] == "Hello, I am a fake reply"       # from the fake client, proving Claude was not really called
    assert data["model"] == "claude-opus-4-8"
    assert data["input_tokens"] == 10


def test_complete_calls_llm_once(client_with_fake_llm: TestClient) -> None:
    """Assert the LLM was actually called (once) with the right args — via the mock's call_count / args."""
    client_with_fake_llm.post(
        "/v1/complete",
        headers={"X-API-Key": VALID_KEY},
        json={"prompt": "hi"},
    )
    create = client_with_fake_llm.fake_llm.messages.create   # type: ignore[attr-defined]
    assert create.call_count == 1
    # Check the args passed to Claude: the prompt is placed correctly into messages
    kwargs = create.call_args.kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_missing_api_key_rejected(client_with_fake_llm: TestClient) -> None:
    """No X-API-Key header -> 422 (missing required header); Claude should not be called."""
    resp = client_with_fake_llm.post("/v1/complete", json={"prompt": "hi"})
    assert resp.status_code == 422
    assert client_with_fake_llm.fake_llm.messages.create.call_count == 0  # type: ignore[attr-defined]


def test_wrong_api_key_rejected(client_with_fake_llm: TestClient) -> None:
    """Wrong key -> 401; the endpoint logic does not run."""
    resp = client_with_fake_llm.post(
        "/v1/complete",
        headers={"X-API-Key": "wrong"},
        json={"prompt": "hi"},
    )
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "bad_body",
    [
        {"prompt": ""},                      # prompt too short (min_length=1)
        {"prompt": "hi", "max_tokens": 0},   # max_tokens must be > 0
        {"prompt": "hi", "max_tokens": 99999},  # exceeds the 8192 upper bound
        {},                                  # missing prompt
    ],
)
def test_invalid_body_rejected(client_with_fake_llm: TestClient, bad_body: dict[str, object]) -> None:
    """parametrize: several dirty request bodies, all should be rejected automatically by Pydantic with 422."""
    resp = client_with_fake_llm.post(
        "/v1/complete",
        headers={"X-API-Key": VALID_KEY},
        json=bad_body,
    )
    assert resp.status_code == 422


def test_upstream_error_becomes_502(client_with_fake_llm: TestClient) -> None:
    """Use side_effect to make the fake client raise Claude's APIStatusError; assert the gateway maps it to 502."""
    import anthropic
    import httpx

    # Build a "real" httpx request/response; APIStatusError needs response.request internally
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(500, request=request)
    err = anthropic.APIStatusError("boom", response=response, body=None)
    client_with_fake_llm.fake_llm.messages.create = AsyncMock(side_effect=err)  # type: ignore[attr-defined]

    resp = client_with_fake_llm.post(
        "/v1/complete",
        headers={"X-API-Key": VALID_KEY},
        json={"prompt": "hi"},
    )
    assert resp.status_code == 502   # upstream error -> gateway returns 502 instead of crashing
