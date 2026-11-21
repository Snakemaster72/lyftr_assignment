import { useState, type FormEvent, type KeyboardEvent } from "react";
import { MAX_MESSAGE_CHARS } from "../config";

interface Props {
  disabled: boolean;
  activeSimulation: string;
  onSend: (message: string) => void;
}

export function Composer({ disabled, activeSimulation, onSend }: Props) {
  const [text, setText] = useState("");
  const tooLong = text.trim().length > MAX_MESSAGE_CHARS;

  function submit() {
    // No client-side trimming or length block: the server is the validator, and its 422 is shown in the chat.
    if (disabled || text.length === 0) return;
    onSend(text);
    setText("");
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit();
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <form className="composer" onSubmit={onSubmit}>
      <textarea
        data-testid="chat-input"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Type a message… (Enter to send, Shift+Enter for a new line)"
        rows={2}
        aria-label="Message"
      />
      <div className="composer-side">
        <button type="submit" data-testid="send-button" disabled={disabled || text.length === 0}>
          Send
        </button>
        <span className={`counter ${tooLong ? "over" : ""}`}>
          {text.trim().length}/{MAX_MESSAGE_CHARS}
        </span>
      </div>
      {activeSimulation && (
        <p className="sim-active">
          Next message is sent with <code>X-Simulate-Failure: {activeSimulation}</code>
        </p>
      )}
    </form>
  );
}
