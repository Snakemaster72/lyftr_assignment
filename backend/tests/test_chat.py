from concurrent.futures import ThreadPoolExecutor

from tests.conftest import chat
from tests.fake_upstream import fake_reply


def test_happy_path_shape_and_types(client, upstream):
    response = chat(client, "s-happy", "What is the capital of France?")
    assert response.status_code == 200
    body = response.json()
    assert body["session_id"] == "s-happy"
    assert body["reply"].startswith("echo: What is the capital of France?")
    meta = body["metadata"]
    assert meta["resolution_source"] == "LIVE_INFERENCE"
    assert meta["model"] == "openai/fake"
    assert meta["key_id"] == "key_1"
    assert meta["fallback_attempts"] == 0
    for field in ("latency_ms", "input_tokens", "output_tokens", "fallback_attempts"):
        assert type(meta[field]) is int, field
    assert meta["input_tokens"] > 0 and meta["output_tokens"] > 0
    assert upstream.chat_requests()[-1]["key"] == "KEY_A"


def test_message_is_trimmed_before_use(client, upstream):
    chat(client, "s-trim", "   hello   ")
    assert upstream.chat_requests()[-1]["body"]["messages"][-1] == {"role": "user", "content": "hello"}
    assert client.get("/sessions/s-trim/messages").json()["messages"][0]["content"] == "hello"


def test_multi_turn_sends_history_oldest_first(client, upstream):
    first = chat(client, "s-multi", "My name is Ada.").json()
    chat(client, "s-multi", "What is my name?")
    sent = upstream.chat_requests()[-1]["body"]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "My name is Ada."
    assert sent[2]["content"] == first["reply"]
    assert sent[3]["content"] == "What is my name?"


def test_sessions_are_isolated(client, upstream):
    chat(client, "s-iso-a", "secret from A")
    chat(client, "s-iso-b", "hello from B")
    sent = upstream.chat_requests()[-1]["body"]["messages"]
    assert all("secret from A" not in m["content"] for m in sent)
    assert len(client.get("/sessions/s-iso-b/messages").json()["messages"]) == 2


def test_tokens_are_per_request_not_cumulative(client, upstream):
    a = chat(client, "s-tok-a", "alpha beta gamma").json()["metadata"]
    b = chat(client, "s-tok-b", "delta omega theta").json()["metadata"]
    assert a["input_tokens"] == b["input_tokens"]
    second = chat(client, "s-tok-a", "one").json()["metadata"]
    # Second turn's prompt includes the history, but output is only this reply's tokens.
    assert second["input_tokens"] > a["input_tokens"]
    assert second["output_tokens"] == len(fake_reply(upstream.chat_requests()[-1]["body"]["messages"]).split())


def test_default_system_prompt_is_used(make_client, upstream):
    client = make_client(default_system_prompt="Be terse.")
    chat(client, "s-sys-default", "hi")
    assert upstream.chat_requests()[-1]["body"]["messages"][0] == {"role": "system", "content": "Be terse."}


def test_session_system_prompt_persists_and_is_hidden_from_history(client, upstream):
    chat(client, "s-sys", "hi", system_prompt="You are a pirate.")
    chat(client, "s-sys", "and again")  # no system_prompt sent this time
    assert upstream.chat_requests()[-1]["body"]["messages"][0]["content"] == "You are a pirate."
    history = client.get("/sessions/s-sys/messages").json()["messages"]
    assert all(m["role"] != "system" for m in history)
    assert all("pirate" not in m["content"] for m in history)


def test_history_cap_keeps_most_recent_turns(make_client, upstream):
    client = make_client(history_max_messages=2)
    for text in ("one", "two", "three"):
        chat(client, "s-cap", text)
    sent = upstream.chat_requests()[-1]["body"]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "two"


def test_history_survives_app_restart(make_client):
    chat(make_client(), "s-restart", "remember me")
    restarted = make_client()  # same database file, fresh process state
    assert len(restarted.get("/sessions/s-restart/messages").json()["messages"]) == 2


def test_unhandled_error_returns_json_without_traceback(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded")

    monkeypatch.setattr(client.app.state.db, "save_turn", boom)
    response = chat(client, "s-boom", "hi")
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    assert "exploded" not in response.text and "Traceback" not in response.text


def test_unknown_path_and_wrong_method_are_json(client):
    assert client.get("/nope").json() == {"detail": "Not Found"}
    response = client.get("/chat")
    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}


def test_cors_preflight_allows_simulation_header(client):
    response = client.options(
        "/chat",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-simulate-failure",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "x-simulate-failure" in response.headers["access-control-allow-headers"].lower()


def test_cors_rejects_unknown_origin(client):
    response = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_concurrent_requests_keep_their_own_simulation(client):
    def send(i: int):
        headers = {"X-Simulate-Failure": "key_1=429"} if i % 2 else {}
        return i, chat(client, f"s-conc-{i}", f"concurrent {i}", headers).json()["metadata"]["key_id"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(send, range(16)))
    for i, key_id in results:
        assert key_id == ("key_2" if i % 2 else "key_1"), i
