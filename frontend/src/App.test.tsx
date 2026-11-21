// @vitest-environment jsdom
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "./App";

const HEALTH = { status: "ok", keys_configured: 2, cache_enabled: true, model: "groq/test", failure_simulation: true };

function jsonResponse(status: number, body: unknown) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function chatReply(source: "LIVE_INFERENCE" | "LLM_CACHE", sessionId = "x") {
  return {
    session_id: sessionId,
    reply: "Paris is the capital.",
    metadata: {
      resolution_source: source,
      model: "groq/openai/gpt-oss-20b",
      latency_ms: 812,
      input_tokens: 42,
      output_tokens: 9,
      key_id: source === "LLM_CACHE" ? null : "key_2",
      fallback_attempts: source === "LLM_CACHE" ? 0 : 1,
    },
  };
}

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;

function mockFetch(chat: Handler) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    return url.endsWith("/health") ? jsonResponse(200, HEALTH) : chat(url, init);
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("renders the reply with every metadata test id holding only its value", async () => {
    mockFetch(() => jsonResponse(200, chatReply("LIVE_INFERENCE")));
    const user = userEvent.setup();
    render(<App />);

    const input = screen.getByTestId("chat-input");
    await user.type(input, "What is the capital of France?");
    await user.click(screen.getByTestId("send-button"));

    const message = await screen.findByTestId("assistant-message");
    expect(input).toHaveProperty("value", "");
    expect(message.textContent).toContain("Paris is the capital.");
    const m = within(message);
    expect(m.getByTestId("meta-source").textContent).toBe("LIVE_INFERENCE");
    expect(m.getByTestId("meta-model").textContent).toBe("groq/openai/gpt-oss-20b");
    expect(m.getByTestId("meta-latency").textContent).toBe("812 ms");
    expect(m.getByTestId("meta-input-tokens").textContent).toBe("42");
    expect(m.getByTestId("meta-output-tokens").textContent).toBe("9");
    expect(message.textContent).toContain("key_2");
    expect(message.textContent).toContain("1 fallback");
  });

  it("labels cache hits", async () => {
    mockFetch(() => jsonResponse(200, chatReply("LLM_CACHE")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hi{Enter}");
    const message = await screen.findByTestId("assistant-message");
    expect(within(message).getByTestId("meta-source").textContent).toBe("LLM_CACHE");
  });

  it("sends with Enter, adds a newline with Shift+Enter, and posts the contract body", async () => {
    const fetchMock = mockFetch(() => jsonResponse(200, chatReply("LIVE_INFERENCE")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "line one{Shift>}{Enter}{/Shift}line two{Enter}");
    await screen.findByTestId("assistant-message");

    const [url, init] = fetchMock.mock.calls.find(([u]) => String(u).endsWith("/chat"))!;
    expect(String(url)).toBe("http://localhost:8000/chat");
    const body = JSON.parse(String(init!.body));
    expect(body.message).toBe("line one\nline two");
    expect(typeof body.session_id).toBe("string");
    expect(body).not.toHaveProperty("system_prompt");
  });

  it("keeps one session id per page load and sends the system prompt and simulation header", async () => {
    const fetchMock = mockFetch(() => jsonResponse(200, chatReply("LIVE_INFERENCE")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("system-prompt-input"), "Be a pirate.");
    await user.selectOptions(screen.getByRole("combobox"), "key_1=429");
    await user.type(screen.getByTestId("chat-input"), "first{Enter}");
    await screen.findByTestId("assistant-message");
    await user.type(screen.getByTestId("chat-input"), "second{Enter}");
    await waitFor(() => expect(screen.getAllByTestId("assistant-message")).toHaveLength(2));

    const chats = fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/chat"));
    const bodies = chats.map(([, init]) => JSON.parse(String(init!.body)));
    expect(bodies[0].session_id).toBe(bodies[1].session_id);
    expect(bodies[0].system_prompt).toBe("Be a pirate.");
    expect((chats[0][1]!.headers as Record<string, string>)["X-Simulate-Failure"]).toBe("key_1=429");
  });

  it("starts a new session on New chat", async () => {
    const fetchMock = mockFetch(() => jsonResponse(200, chatReply("LIVE_INFERENCE")));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "a{Enter}");
    await screen.findByTestId("assistant-message");
    await user.click(screen.getByRole("button", { name: "New chat" }));
    expect(screen.queryByTestId("assistant-message")).toBeNull();
    await user.type(screen.getByTestId("chat-input"), "b{Enter}");
    await screen.findByTestId("assistant-message");
    const ids = fetchMock.mock.calls
      .filter(([u]) => String(u).endsWith("/chat"))
      .map(([, init]) => JSON.parse(String(init!.body)).session_id);
    expect(ids[0]).not.toBe(ids[1]);
  });

  it.each([
    [422, { detail: [{ loc: ["body", "message"], msg: "Value error, message must not be empty" }] }, /Invalid request: message: message must not be empty/],
    [503, { detail: "all providers unavailable" }, /All providers are unavailable/],
  ])("shows a readable %i error in the chat", async (status, body, expected) => {
    mockFetch(() => jsonResponse(status, body));
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "   x{Enter}");
    const error = await screen.findByTestId("error-message");
    expect(error.textContent).toMatch(expected);
    expect(screen.queryByTestId("assistant-message")).toBeNull();
    expect(screen.getByTestId("chat-input")).toHaveProperty("value", "");
  });

  it("keeps retrying the health check while a sleeping backend boots", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      let healthCalls = 0;
      vi.stubGlobal(
        "fetch",
        vi.fn(async (input: RequestInfo | URL) => {
          if (!String(input).endsWith("/health")) throw new Error("unexpected");
          healthCalls += 1;
          if (healthCalls === 1) throw new TypeError("Failed to fetch");
          return jsonResponse(200, HEALTH);
        }),
      );
      render(<App />);
      expect((await screen.findByText(/backend unreachable/)).textContent).toMatch(/retrying/);
      await vi.advanceTimersByTimeAsync(5000);
      expect(await screen.findByText("groq/test · 2 keys")).toBeTruthy();
      expect(healthCalls).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows a readable message when the backend is unreachable", async () => {
    mockFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const user = userEvent.setup();
    render(<App />);
    await user.type(screen.getByTestId("chat-input"), "hello{Enter}");
    expect((await screen.findByTestId("error-message")).textContent).toMatch(/Can't reach the server/);
  });
});
