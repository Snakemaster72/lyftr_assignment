import { useCallback, useEffect, useState } from "react";
import { ApiError, getHealth, sendChat } from "./api";
import { Composer } from "./components/Composer";
import { MessageList } from "./components/MessageList";
import { SettingsPanel } from "./components/SettingsPanel";
import { API_BASE_URL } from "./config";
import type { Health, UiMessage } from "./types";

/** crypto.randomUUID needs a secure context (https or localhost); fall back elsewhere. */
export function newSessionId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

const HEALTH_RETRY_MS = 5000;

let nextId = 0;
const uid = () => `m${++nextId}`;

export default function App() {
  // A new session on every page load, kept in memory only.
  const [sessionId, setSessionId] = useState(newSessionId);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [pending, setPending] = useState(false);
  const [systemPrompt, setSystemPrompt] = useState("");
  const [preset, setPreset] = useState("");
  const [customSimulation, setCustomSimulation] = useState("");
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);

  // A sleeping free-tier backend fails the first check while it boots (~1 min), so keep retrying.
  const checkHealth = useCallback(() => {
    getHealth()
      .then((h) => {
        setHealth(h);
        setHealthError(false);
      })
      .catch(() => {
        setHealth(null);
        setHealthError(true);
      });
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  useEffect(() => {
    if (!healthError) return;
    const timer = setTimeout(checkHealth, HEALTH_RETRY_MS);
    return () => clearTimeout(timer);
  }, [healthError, checkHealth]);

  const simulation = preset === "custom" ? customSimulation.trim() : preset;

  async function handleSend(text: string) {
    setMessages((prev) => [...prev, { id: uid(), kind: "user", content: text }]);
    setPending(true);
    try {
      const res = await sendChat({ sessionId, message: text, systemPrompt, simulateFailure: simulation });
      setMessages((prev) => [...prev, { id: uid(), kind: "assistant", content: res.reply, metadata: res.metadata }]);
      if (!health) checkHealth();
    } catch (err) {
      const content = err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      setMessages((prev) => [...prev, { id: uid(), kind: "error", content }]);
    } finally {
      setPending(false);
    }
  }

  function newChat() {
    setSessionId(newSessionId());
    setMessages([]);
  }

  return (
    <div className="app">
      <header className="header">
        <div className="title">
          <h1>Lyftr Chat</h1>
          <span className="session" title={sessionId}>
            session {sessionId.slice(0, 8)}
          </span>
        </div>
        <div className="header-right">
          <span
            className={`status ${health?.status === "ok" ? "ok" : healthError ? "down" : ""}`}
            title={`Backend: ${API_BASE_URL}`}
          >
            {health
              ? `${health.model ?? "backend"} · ${health.keys_configured} keys`
              : healthError
                ? "backend unreachable · retrying"
                : "connecting…"}
          </span>
          <button type="button" className="secondary" onClick={newChat} disabled={pending}>
            New chat
          </button>
        </div>
      </header>

      <SettingsPanel
        systemPrompt={systemPrompt}
        onSystemPromptChange={setSystemPrompt}
        preset={preset}
        onPresetChange={setPreset}
        customSimulation={customSimulation}
        onCustomSimulationChange={setCustomSimulation}
        health={health}
      />

      <MessageList messages={messages} pending={pending} />

      <Composer disabled={pending} activeSimulation={simulation} onSend={handleSend} />
    </div>
  );
}
