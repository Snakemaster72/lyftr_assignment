from tests.conftest import chat, wait_for_cache_write

SIM = "X-Simulate-Failure"


def test_same_first_message_in_new_session_is_a_cache_hit(client, upstream):
    live = chat(client, "s-cache-1", "Tell me about caching").json()
    wait_for_cache_write()
    cached = chat(client, "s-cache-2", "Tell me about caching").json()

    assert live["metadata"]["resolution_source"] == "LIVE_INFERENCE"
    meta = cached["metadata"]
    assert meta["resolution_source"] == "LLM_CACHE"
    assert cached["reply"] == live["reply"]
    assert meta["key_id"] is None
    assert meta["fallback_attempts"] == 0
    assert meta["input_tokens"] == live["metadata"]["input_tokens"]
    assert meta["output_tokens"] == live["metadata"]["output_tokens"]
    assert meta["model"] == live["metadata"]["model"]
    assert meta["latency_ms"] < live["metadata"]["latency_ms"]
    assert len(upstream.chat_requests()) == 1


def test_cache_hit_is_stored_in_history(client):
    chat(client, "s-ch-a", "store the hit")
    wait_for_cache_write()
    chat(client, "s-ch-b", "store the hit")
    stored = client.get("/sessions/s-ch-b/messages").json()["messages"][1]["metadata"]
    assert stored["resolution_source"] == "LLM_CACHE"
    assert stored["key_id"] is None


def test_different_message_is_never_cached(client):
    chat(client, "s-diff-1", "first question")
    wait_for_cache_write()
    assert chat(client, "s-diff-2", "second question").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_same_message_with_different_history_is_not_cached(client):
    chat(client, "s-hist-a", "hello")
    chat(client, "s-hist-a", "and then?")
    wait_for_cache_write()
    # Same new message, but the full message list differs (no prior turns).
    assert chat(client, "s-hist-b", "and then?").json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_different_system_prompt_is_not_cached(client):
    chat(client, "s-sp-a", "same text", system_prompt="Persona one.")
    wait_for_cache_write()
    response = chat(client, "s-sp-b", "same text", system_prompt="Persona two.")
    assert response.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"


def test_cache_is_shared_across_keys(client):
    via_key_2 = chat(client, "s-shared-1", "shared entry", {SIM: "key_1=429"}).json()
    assert via_key_2["metadata"]["key_id"] == "key_2"
    wait_for_cache_write()
    hit = chat(client, "s-shared-2", "shared entry").json()
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == via_key_2["reply"]


def test_simulated_request_bypasses_cache_read(client):
    chat(client, "s-bypass-1", "cached already")
    wait_for_cache_write()
    assert chat(client, "s-bypass-2", "cached already").json()["metadata"]["resolution_source"] == "LLM_CACHE"

    failover = chat(client, "s-bypass-3", "cached already", {SIM: "key_1=429"}).json()["metadata"]
    assert failover["resolution_source"] == "LIVE_INFERENCE"
    assert failover["key_id"] == "key_2"

    all_fail = chat(client, "s-bypass-4", "cached already", {SIM: "*=429"})
    assert all_fail.status_code == 503


def test_empty_reply_is_never_cached(make_client):
    client = make_client(llm_api_keys="EMPTY,KEY_B")
    first = chat(client, "s-empty-1", "empty please").json()
    assert first["metadata"]["key_id"] == "key_2"
    wait_for_cache_write()
    hit = chat(client, "s-empty-2", "empty please").json()
    assert hit["metadata"]["resolution_source"] == "LLM_CACHE"
    assert hit["reply"] == first["reply"] != ""
