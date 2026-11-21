import pytest

from tests.conftest import chat

LONG = "x" * 4001


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"session_id": "s-v"}, id="missing-message"),
        pytest.param({"message": "hi"}, id="missing-session-id"),
        pytest.param({"session_id": "s-v", "message": ""}, id="empty-message"),
        pytest.param({"session_id": "s-v", "message": "   \n\t "}, id="whitespace-message"),
        pytest.param({"session_id": "s-v", "message": LONG}, id="message-4001"),
        pytest.param({"session_id": "", "message": "hi"}, id="empty-session-id"),
        pytest.param({"session_id": "   ", "message": "hi"}, id="whitespace-session-id"),
        pytest.param({"session_id": "s" * 129, "message": "hi"}, id="session-id-129"),
        pytest.param({"session_id": "s-v", "message": 123}, id="message-not-string"),
        pytest.param({"session_id": 42, "message": "hi"}, id="session-id-not-string"),
        pytest.param({"session_id": "s-v", "message": "hi", "system_prompt": "p" * 4001}, id="system-prompt-4001"),
        pytest.param([], id="body-not-object"),
    ],
)
def test_invalid_bodies_return_422_and_write_nothing(client, upstream, body):
    response = client.post("/chat", json=body)
    assert response.status_code == 422
    assert "detail" in response.json()
    assert upstream.chat_requests() == []
    assert client.get("/sessions/s-v/messages").status_code == 404


def test_malformed_json_returns_422(client, upstream):
    response = client.post(
        "/chat", content=b'{"session_id": "s-v", "message": ', headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422
    assert "detail" in response.json()
    assert upstream.chat_requests() == []


def test_limits_apply_after_trimming(client):
    assert chat(client, "s-edge", "  " + "y" * 4000 + "  ").status_code == 200
    assert chat(client, "s" * 128, "hi").status_code == 200


def test_422_on_existing_session_leaves_history_unchanged(client):
    chat(client, "s-keep", "first")
    assert chat(client, "s-keep", " ").status_code == 422
    assert len(client.get("/sessions/s-keep/messages").json()["messages"]) == 2
