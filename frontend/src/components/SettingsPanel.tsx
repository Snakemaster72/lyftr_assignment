import type { Health } from "../types";

export const SIMULATION_PRESETS = [
  { value: "", label: "None" },
  { value: "key_1=429", label: "key_1 = 429 (rate limit)" },
  { value: "key_1=500", label: "key_1 = 500 (server error)" },
  { value: "*=429", label: "* = 429 (every key fails)" },
  { value: "custom", label: "Custom…" },
] as const;

interface Props {
  systemPrompt: string;
  onSystemPromptChange: (value: string) => void;
  preset: string;
  onPresetChange: (value: string) => void;
  customSimulation: string;
  onCustomSimulationChange: (value: string) => void;
  health: Health | null;
}

export function SettingsPanel(props: Props) {
  const simulationState =
    props.health?.failure_simulation === undefined
      ? null
      : props.health.failure_simulation
        ? "enabled on this backend"
        : "disabled on this backend: the header will be ignored";

  return (
    <div className="settings">
      <details>
        <summary>System prompt</summary>
        <textarea
          data-testid="system-prompt-input"
          value={props.systemPrompt}
          onChange={(e) => props.onSystemPromptChange(e.target.value)}
          placeholder="Optional. Sent with each message; it becomes this session's system prompt."
          rows={3}
          aria-label="System prompt"
        />
      </details>
      <details>
        <summary>Failover demo</summary>
        <div className="sim">
          <label>
            X-Simulate-Failure
            <select value={props.preset} onChange={(e) => props.onPresetChange(e.target.value)}>
              {SIMULATION_PRESETS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          {props.preset === "custom" && (
            <input
              type="text"
              value={props.customSimulation}
              onChange={(e) => props.onCustomSimulationChange(e.target.value)}
              placeholder="key_1=429,key_2=500"
              aria-label="Custom X-Simulate-Failure value"
            />
          )}
          <p className="note">
            Makes the chosen keys fail for each message while selected. Works only when the backend runs with{" "}
            <code>ENABLE_FAILURE_SIMULATION=true</code>
            {simulationState && <> ({simulationState})</>}. Simulated requests skip the cache read so failover
            always runs.
          </p>
        </div>
      </details>
    </div>
  );
}
