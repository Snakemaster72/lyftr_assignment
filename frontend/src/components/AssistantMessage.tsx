import type { ChatMetadata } from "../types";

interface Props {
  content: string;
  metadata: ChatMetadata;
}

/** One assistant reply. Each meta-* element holds only its value; labels sit outside it. */
export function AssistantMessage({ content, metadata: m }: Props) {
  const cached = m.resolution_source === "LLM_CACHE";
  return (
    <article className="msg assistant" data-testid="assistant-message">
      <div className="bubble">{content}</div>
      <div className="meta" aria-label="Response metadata">
        <span className={`badge ${cached ? "badge-cache" : "badge-live"}`} data-testid="meta-source">
          {m.resolution_source}
        </span>
        <span className="meta-item" title="Model">
          <span className="label">model</span> <span data-testid="meta-model">{m.model}</span>
        </span>
        <span className="meta-item" title="Server-measured latency">
          <span className="label">latency</span> <span data-testid="meta-latency">{m.latency_ms} ms</span>
        </span>
        <span className="meta-item" title="Prompt tokens for this request">
          <span className="label">in</span> <span data-testid="meta-input-tokens">{m.input_tokens}</span>
        </span>
        <span className="meta-item" title="Completion tokens for this reply">
          <span className="label">out</span> <span data-testid="meta-output-tokens">{m.output_tokens}</span>
        </span>
        <span className="badge badge-muted" title="Key that answered">
          {m.key_id ?? "no key (cache)"}
        </span>
        <span
          className={`badge ${m.fallback_attempts > 0 ? "badge-warn" : "badge-muted"}`}
          title="Keys that failed before this one answered"
        >
          {m.fallback_attempts} fallback{m.fallback_attempts === 1 ? "" : "s"}
        </span>
      </div>
    </article>
  );
}
