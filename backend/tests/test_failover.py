import pytest

from tests.conftest import chat

SIM = "X-Simulate-Failure"


def history_len(client, session_id):
    response = client.get(f"/sessions/{session_id}/messages")
    return 0 if response.status_code == 404 else len(response.json()["messages"])


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_simulated_key_1_failure_fails_over_to_key_2(client, upstream, status):
    response = chat(client, f"s-sim-{status}", f"failover {status}", {SIM: f"key_1={status}"})
    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["key_id"] == "key_2"
    assert body["metadata"]["fallback_attempts"] == 1
    assert body["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    # A real answer from the provider, never an error wrapped in a 200.
    assert body["reply"].startswith(f"echo: failover {status}")
    assert [r["key"] for r in upstream.chat_requests()] == ["KEY_B"]


def test_all_keys_failing_returns_503_and_writes_nothing(client):
    chat(client, "s-all", "before")
    response = chat(client, "s-all", "doomed", {SIM: "*=429"})
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert history_len(client, "s-all") == 2
    assert chat(client, "s-all-new", "doomed", {SIM: "*=429"}).status_code == 503
    assert client.get("/sessions/s-all-new/messages").status_code == 404


def test_every_listed_key_failing_returns_503(client):
    response = chat(client, "s-both", "both fail", {SIM: "key_1=429,key_2=500"})
    assert response.status_code == 503


def test_three_keys_fail_over_in_order(make_client):
    client = make_client(llm_api_keys="A,B,C")
    meta = chat(client, "s-three", "go", {SIM: "key_1=429,key_2=502"}).json()["metadata"]
    assert meta["key_id"] == "key_3"
    assert meta["fallback_attempts"] == 2


def test_unknown_key_ids_are_ignored(client):
    meta = chat(client, "s-unknown", "hi", {SIM: "key_9=429"}).json()["metadata"]
    assert meta["key_id"] == "key_1"


def test_simulation_does_not_leak_into_next_request(client):
    assert chat(client, "s-leak", "first", {SIM: "key_1=429"}).json()["metadata"]["key_id"] == "key_2"
    meta = chat(client, "s-leak", "second").json()["metadata"]
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0


def test_simulation_ignored_when_disabled(make_client):
    client = make_client(enable_failure_simulation=False)
    for header in ("*=429", "not a valid header"):
        response = chat(client, f"s-off-{header}", f"hi {header}", {SIM: header})
        assert response.status_code == 200
        assert response.json()["metadata"]["key_id"] == "key_1"


@pytest.mark.parametrize(
    "header", ["bogus", "key_1", "key_1=", "=429", "key_1=abc", "key_1=404", "key_1=429;key_2=500"]
)
def test_malformed_simulation_header_is_422(client, upstream, header):
    response = chat(client, "s-bad-header", "hi", {SIM: header})
    assert response.status_code == 422
    assert "X-Simulate-Failure" in response.json()["detail"]
    assert upstream.chat_requests() == []
    assert client.get("/sessions/s-bad-header/messages").status_code == 404


@pytest.mark.parametrize("bad_key", ["FAIL429", "FAIL500", "FAIL401", "EMPTY"])
def test_real_upstream_failure_on_key_1_fails_over(make_client, upstream, bad_key):
    client = make_client(llm_api_keys=f"{bad_key},KEY_B")
    response = chat(client, f"s-real-{bad_key}", "real failure")
    assert response.status_code == 200
    meta = response.json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["fallback_attempts"] == 1
    assert response.json()["reply"]
    assert [r["key"] for r in upstream.chat_requests()] == [bad_key, "KEY_B"]


def test_slow_key_times_out_and_fails_over(make_client):
    client = make_client(llm_api_keys="SLOW,KEY_B", llm_timeout_seconds=0.5)
    meta = chat(client, "s-slow", "are you there?").json()["metadata"]
    assert meta["key_id"] == "key_2"
    assert meta["latency_ms"] < 2500


@pytest.mark.parametrize("keys", ["FAIL429,FAIL500", "EMPTY,EMPTY", "FAIL401,FAIL401"])
def test_all_real_upstream_failures_return_503(make_client, keys):
    client = make_client(llm_api_keys=keys)
    response = chat(client, "s-real-all", "nobody home")
    assert response.status_code == 503
    assert response.json() == {"detail": "all providers unavailable"}
    assert client.get("/sessions/s-real-all/messages").status_code == 404
