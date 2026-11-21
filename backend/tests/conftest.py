"""Fixtures: a fake OpenAI-compatible upstream and app clients wired to it.

Nothing inside LiteLLM is mocked: requests go through the real Router, cache,
failover and exception mapping, then over HTTP to the fake server.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings
from app.main import create_app
from tests.fake_upstream import FakeUpstream

# Variables that would leak from a developer's shell or .env into the tests.
_ENV_VARS = (
    "LLM_API_KEYS LLM_MODEL LLM_MODELS LLM_API_BASE DATABASE_URL ALLOWED_ORIGINS ENABLE_FAILURE_SIMULATION "
    "DEFAULT_SYSTEM_PROMPT CACHE_TYPE CACHE_DIR CACHE_TTL_SECONDS CACHE_MAX_ITEMS LLM_TIMEOUT_SECONDS "
    "HISTORY_MAX_MESSAGES ENABLE_DOCS LOG_LEVEL"
).split()

CACHE_WRITE_WAIT_SECONDS = 0.2  # LiteLLM writes the cache asynchronously after responding


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(scope="session")
def upstream() -> Iterator[FakeUpstream]:
    server = FakeUpstream().start()
    yield server
    server.stop()


def make_settings(upstream: FakeUpstream, db_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "llm_api_keys": "KEY_A,KEY_B",
        "llm_model": "openai/fake",
        "llm_api_base": upstream.base_url,
        "database_url": f"sqlite:///{db_path}",
        "enable_failure_simulation": True,
        "log_level": "WARNING",
        "_env_file": None,
    }
    values.update(overrides)
    return load_settings(**values)


@pytest.fixture
def make_client(upstream: FakeUpstream, tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    upstream.reset()
    clients: list[TestClient] = []

    def _make(**overrides: Any) -> TestClient:
        settings = make_settings(upstream, tmp_path / "app.db", **overrides)
        client = TestClient(create_app(settings))
        client.__enter__()  # runs the lifespan (schema, router, cache warm-up)
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> TestClient:
    return make_client()


def chat(client: TestClient, session_id: str, message: str, headers: dict[str, str] | None = None, **extra: Any):
    return client.post("/chat", json={"session_id": session_id, "message": message, **extra}, headers=headers or {})


def wait_for_cache_write() -> None:
    time.sleep(CACHE_WRITE_WAIT_SECONDS)
