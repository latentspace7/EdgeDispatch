import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import { policySchema } from "@/lib/types";
import type { Health, Preferences } from "@/lib/types";

interface Props {
  preferences: Preferences;
  health: Health | null;
  onSave: (value: Preferences) => Promise<void>;
  onClose: () => void;
}
export default function SettingsDialog({
  preferences,
  health,
  onSave,
  onClose,
}: Props) {
  const [draft, setDraft] = useState(preferences);
  const [saving, setSaving] = useState(false);
  const savingRef = useRef(false);
  const [error, setError] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  async function save() {
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    try {
      await onSave(draft);
    } catch (error) {
      setError(errorMessage(error));
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      onCancel={(event) => {
        event.preventDefault();
        if (!saving) onClose();
      }}
      aria-labelledby="settings-title"
      aria-busy={saving}
      className="settings-dialog"
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <div className="dialog-title">
          <h2 id="settings-title">Settings & connections</h2>
          <button
            type="button"
            disabled={saving}
            onClick={onClose}
            aria-label="Close settings"
          >
            ×
          </button>
        </div>
        <p className="muted">
          The local model is detected from llama.cpp. Remote credentials are
          configured on the backend. Your preferences are saved when you choose
          Save preferences.
        </p>
        <label>
          Policy for new conversations
          <select
            value={draft.policy}
            onChange={(e) =>
              setDraft({ ...draft, policy: policySchema.parse(e.target.value) })
            }
          >
            <option value="sticky_escalation">
              Stay remote after escalation
            </option>
            <option value="reconsider_each_turn">
              Classify every new request
            </option>
          </select>
        </label>
        <h3>Connections</h3>
        <dl className="connections">
          <dt>Local execution</dt>
          <dd>
            {health?.local_model_name || "Unknown"} ·{" "}
            {health?.local_model ? "ready" : "setup required"}
          </dd>
          <dt>Remote execution</dt>
          <dd>
            {health?.remote_model_name || "Unknown"} ·{" "}
            {health?.remote_access_error ||
              (health?.remote_access_tested
                ? "connected - response received"
                : health?.remote_configured
                  ? "key configured, access untested"
                  : "key required")}
          </dd>
          <dt>Redis</dt>
          <dd>{health?.redis ? "connected" : "not connected"}</dd>
          <dt>MCP servers</dt>
          <dd>
            {health?.mcp_servers.join(", ") || "None"}
            {health?.unavailable_mcp_servers.length
              ? ` · Missing: ${health.unavailable_mcp_servers.join(", ")}`
              : ""}
          </dd>
        </dl>
        <h3>Text API pricing (USD per million tokens)</h3>
        <p className="muted">
          Leave blank until you have confirmed your model’s rates. Unconfigured
          or missing usage is shown as unknown, never free. Standard text rates
          only; special tiers and paid tools need separate accounting.
        </p>
        <div className="pricing-grid">
          <label>
            Exact model ID
            <input
              value={draft.pricing_model}
              onChange={(e) =>
                setDraft({ ...draft, pricing_model: e.target.value })
              }
            />
          </label>
          <label>
            Pricing version / date
            <input
              placeholder="e.g. Standard rates, 2026-09-08"
              value={draft.pricing_version}
              onChange={(e) =>
                setDraft({ ...draft, pricing_version: e.target.value })
              }
            />
          </label>
          {(["input_rate", "cached_input_rate", "output_rate"] as const).map(
            (key) => (
              <label key={key}>
                {key.replaceAll("_", " ")}
                <input
                  type="number"
                  min="0"
                  step="any"
                  value={draft[key] ?? ""}
                  onChange={(e) =>
                    setDraft({
                      ...draft,
                      [key]:
                        e.target.value === "" ? null : Number(e.target.value),
                    })
                  }
                />
              </label>
            ),
          )}
        </div>
        {error && (
          <p role="alert" className="error-text">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button type="button" disabled={saving} onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={saving}>
            {saving ? "Saving…" : "Save preferences"}
          </button>
        </div>
      </form>
    </dialog>
  );
}
