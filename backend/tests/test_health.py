from pathlib import Path

import pytest

from app.config import ConfigError, load_settings


def test_health_shape(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["keys_configured"] == 2
    assert body["cache_enabled"] is True
    assert body["failure_simulation"] is True
    assert response.headers["X-Request-ID"]


def test_health_counts_every_key(make_client):
    assert make_client(llm_api_keys="A,B,C").get("/health").json()["keys_configured"] == 3


def test_health_reports_unreachable_database(client, monkeypatch):
    monkeypatch.setattr(client.app.state.db, "ping", lambda: False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["detail"] == "database unreachable"


def test_startup_requires_two_keys_and_never_echoes_them():
    with pytest.raises(ConfigError) as excinfo:
        load_settings(llm_api_keys="gsk_secret_value", _env_file=None)
    assert "at least 2" in str(excinfo.value)
    assert "gsk_secret_value" not in str(excinfo.value)


def test_llm_models_must_match_key_count():
    with pytest.raises(ConfigError, match="one model per key"):
        load_settings(llm_api_keys="A,B", llm_models="m1", _env_file=None)


@pytest.mark.parametrize(
    ("url", "path"),
    [
        ("sqlite:////data/app.db", Path("/data/app.db")),
        ("sqlite:///./data/app.db", Path("./data/app.db")),
        ("sqlite:///app.db", Path("app.db")),
    ],
)
def test_database_url_parsing(url, path):
    assert load_settings(llm_api_keys="A,B", database_url=url, _env_file=None).database_path == path


def test_database_file_and_parent_dir_are_created(make_client, tmp_path):
    db_path = tmp_path / "nested" / "dir" / "app.db"
    make_client(database_url=f"sqlite:///{db_path}")
    assert db_path.exists()


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_docs_disabled_by_default(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_docs_can_be_enabled(make_client):
    assert make_client(enable_docs=True).get("/openapi.json").status_code == 200


def test_only_contract_paths_exist(make_client):
    paths = make_client(enable_docs=True).get("/openapi.json").json()["paths"]
    assert set(paths) == {"/health", "/chat", "/sessions/{session_id}/messages"}
