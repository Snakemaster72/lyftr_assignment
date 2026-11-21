from tests.conftest import chat


def test_history_order_and_shape(client):
    first = chat(client, "s-hist", "one").json()
    second = chat(client, "s-hist", "two").json()
    body = client.get("/sessions/s-hist/messages").json()
    assert body["session_id"] == "s-hist"
    messages = body["messages"]
    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert messages[0] == {"role": "user", "content": "one"}
    assert messages[1]["content"] == first["reply"]
    assert messages[1]["metadata"] == first["metadata"]
    assert messages[3]["metadata"] == second["metadata"]
    assert "metadata" not in messages[2]


def test_unknown_session_is_404(client):
    response = client.get("/sessions/does-not-exist/messages")
    assert response.status_code == 404
    assert response.json() == {"detail": "session not found"}
