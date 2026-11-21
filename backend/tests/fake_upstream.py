"""A tiny OpenAI-compatible server used by the tests instead of a real provider.

Behaviour is chosen by the bearer key so each deployment in the Router can be
made to fail independently:

    FAIL429 -> 429 rate limit      FAIL500 -> 500 server error
    FAIL401 -> 401 bad key         EMPTY   -> 200 with empty content
    anything else -> 200 with a deterministic reply

Every request is recorded so tests can inspect the payload that was sent.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LIVE_DELAY_SECONDS = 0.05  # makes "cache hit is faster than live" measurable


def fake_reply(messages: list[dict[str, Any]]) -> str:
    last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return f"echo: {last_user} (turns={len(messages)})"


def count_tokens(text: str) -> int:
    return max(1, len(text.split()))


class FakeUpstream:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # silence stderr
                pass

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                key = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
                with upstream._lock:
                    upstream.requests.append({"path": self.path, "key": key, "body": body})

                if key == "FAIL429":
                    return self._send(429, {"error": {"message": "rate limited", "type": "rate_limit"}})
                if key == "FAIL500":
                    return self._send(500, {"error": {"message": "boom", "type": "server_error"}})
                if key == "FAIL401":
                    return self._send(401, {"error": {"message": "bad key", "type": "auth"}})

                time.sleep(LIVE_DELAY_SECONDS)
                messages = body.get("messages", [])
                content = "" if key == "EMPTY" else fake_reply(messages)
                prompt_tokens = sum(count_tokens(m.get("content") or "") for m in messages)
                completion_tokens = count_tokens(content) if content else 0
                self._send(
                    200,
                    {
                        "id": f"chatcmpl-{len(upstream.requests)}",
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": body.get("model", "fake"),
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": prompt_tokens + completion_tokens,
                        },
                    },
                )

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def chat_requests(self) -> list[dict[str, Any]]:
        with self._lock:
            return [r for r in self.requests if r["path"].endswith("/chat/completions")]

    def reset(self) -> None:
        with self._lock:
            self.requests.clear()

    def start(self) -> FakeUpstream:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
