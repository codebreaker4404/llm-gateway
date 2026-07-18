# llm-gateway

A small, production-style **API gateway that sits in front of the Claude API**. Clients send a prompt to one authenticated endpoint; the gateway validates the request, forwards it to Claude, maps upstream failures to sane HTTP status codes, and returns the completion with token usage.

Built as a focused example of the fundamentals a backend/AI engineer is expected to ship: typed code, request validation, dependency injection, auth, structured error handling, unit tests that mock the external LLM, containerization, and CI.

![CI](https://github.com/junjieli-ucf/llm-gateway/actions/workflows/ci.yml/badge.svg)

---

## What it does

`POST /v1/complete` takes a prompt, calls Claude, and returns the generated text:

```bash
curl -X POST http://127.0.0.1:8000/v1/complete \
  -H "X-API-Key: my-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Explain what an API gateway is in one sentence.", "max_tokens": 100}'
```

```json
{
  "text": "An API gateway is a single entry point that ...",
  "model": "claude-opus-4-8",
  "input_tokens": 18,
  "output_tokens": 42
}
```

`GET /healthz` is an unauthenticated liveness probe for deployment/CI.

## Highlights

| Concern | How it's handled |
|---|---|
| **Input validation** | Pydantic models with field constraints (`min_length`, `gt`, `le`); invalid bodies return `422` automatically |
| **Auth** | `X-API-Key` header checked via a FastAPI dependency; wrong/missing key → `401`/`422` |
| **Concurrency** | `async` endpoint + `await`ed Claude call, so the server handles other requests while one is in flight |
| **Shared resources** | The Claude client is created once in a `lifespan` handler and reused via dependency injection (not rebuilt per request) |
| **Error mapping** | Upstream failures are translated: Claude error status → `502`, connection failure → `503` (the gateway never leaks a 500 stack trace) |
| **Observability** | Request-logging middleware records method, path, status, and latency |
| **Testability** | The Claude client is a swappable dependency, so tests inject a mock and run with **no network and no API key** |

## Tech stack

Python 3.12 · FastAPI · Pydantic v2 · Anthropic SDK (`AsyncAnthropic`) · pytest · ruff · mypy (`--strict`) · Docker · GitHub Actions

---

## Run it locally

Dependencies are managed with [uv](https://docs.astral.sh/uv/).

```bash
uv sync --dev

# Two separate keys (see note below):
export ANTHROPIC_API_KEY=sk-...      # used BY the gateway to call Claude
export GATEWAY_API_KEY=my-secret     # required FROM callers of the gateway

uv run uvicorn main:app --reload
```

Open the interactive docs at <http://127.0.0.1:8000/docs>.

> **Two keys, two directions.** `ANTHROPIC_API_KEY` is what the gateway uses to authenticate **to Claude** (upstream). `GATEWAY_API_KEY` is what **callers must present** to use the gateway (downstream). The gateway is a client to Claude and a server to its users.

## Test it

The test suite mocks the Claude call, so it needs **no API key and no network** — it runs the same locally and in CI.

```bash
uv run pytest -v          # unit tests (auth, validation, success, upstream-error, health)
uv run ruff check .       # lint
uv run mypy --strict main.py   # strict type check
```

These three commands are exactly what CI runs on every push.

## Run in Docker

```bash
docker build -t llm-gateway .
docker run -p 8000:8000 \
  -e ANTHROPIC_API_KEY=sk-... \
  -e GATEWAY_API_KEY=my-secret \
  llm-gateway
```

Or with Compose (reads `ANTHROPIC_API_KEY` from your shell):

```bash
docker compose up --build
```

---

## Project layout

```
.
├── main.py                     # the FastAPI app (models, deps, endpoints, error handling)
├── tests/
│   └── test_main.py            # unit tests; mocks Claude via dependency override
├── Dockerfile                  # slim image, layer caching, non-root user
├── docker-compose.yml
├── pyproject.toml              # deps + tooling config
└── .github/workflows/ci.yml    # ruff + mypy + pytest on every push
```

## How the tests mock Claude

`get_llm` returns the shared Claude client as a FastAPI dependency. Tests override it with
`app.dependency_overrides[get_llm]`, injecting an `AsyncMock` that returns a canned response.
That lets the suite assert on real gateway behavior — status codes, validation, call counts,
and upstream-error translation — without spending a token or touching the network.
