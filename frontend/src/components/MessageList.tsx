import { useEffect, useRef } from "react";
import type { UiMessage } from "../types";
import { AssistantMessage } from "./AssistantMessage";

interface Props {
  messages: UiMessage[];
  pending: boolean;
}

export function MessageList({ messages, pending }: Props) {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, pending]);

  return (
    <section className="messages" aria-live="polite">
      {messages.length === 0 && !pending && (
        <p className="empty">Start the conversation. Each page load is a new session.</p>
      )}
      {messages.map((m) => {
        switch (m.kind) {
          case "user":
            return (
              <article key={m.id} className="msg user" data-testid="user-message">
                <div className="bubble">{m.content}</div>
              </article>
            );
          case "assistant":
            return <AssistantMessage key={m.id} content={m.content} metadata={m.metadata} />;
          case "error":
            return (
              <article key={m.id} className="msg error" role="alert" data-testid="error-message">
                <div className="bubble">{m.content}</div>
              </article>
            );
        }
      })}
      {pending && (
        <article className="msg assistant thinking" data-testid="thinking">
          <div className="bubble">
            Thinking<span className="dots" aria-hidden="true" />
          </div>
        </article>
      )}
      <div ref={endRef} />
    </section>
  );
}
