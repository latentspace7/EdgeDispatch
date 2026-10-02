import { useCallback, useEffect, useState } from "react";
import {
  api,
  errorMessage,
  createConversation,
  getHealth,
  getSettings,
  listConversations,
  retryDelivery,
  sendTurn,
} from "@/lib/api";
import { isActive, preferencesSchema } from "@/lib/types";
import type {
  ConversationSummary,
  Health,
  Preferences,
  Policy,
} from "@/lib/types";
import { useChat } from "@/hooks/useChat";
import Sidebar from "@/components/Sidebar";
import ChatInterface from "@/components/ChatInterface";
import SettingsDialog from "@/components/SettingsDialog";

const money = (value: string | number | null | undefined) =>
  value == null ? "Not available" : `$${Number(value).toFixed(5)}`;

export default function App() {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [preferences, setPreferences] = useState<Preferences | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [insightsOpen, setInsightsOpen] = useState(false);
  const refreshList = useCallback(() => {
    void listConversations()
      .then(setConversations)
      .catch((error) => setError(errorMessage(error)));
  }, []);
  const {
    conversation,
    refresh,
    loading,
    error: streamError,
  } = useChat(activeId, refreshList);
  const metrics = conversation?.metrics;
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      listConversations(controller.signal),
      getHealth(controller.signal),
      getSettings(controller.signal),
    ])
      .then(([list, health, preferences]) => {
        if (controller.signal.aborted) return;
        setConversations(list);
        setHealth(health);
        setPreferences(preferences);
        const saved = localStorage.getItem("edgedispatch-active-thread");
        setActiveId(
          list.some((c) => c.id === saved) ? saved : list[0]?.id || null,
        );
      })
      .catch((error) => {
        if (!controller.signal.aborted) setError(errorMessage(error));
      });
    return () => {
      controller.abort();
    };
  }, []);

  function select(id: string) {
    setActiveId(id);
    localStorage.setItem("edgedispatch-active-thread", id);
    setError("");
  }
  function openSettings() {
    setSettingsOpen(true);
    void getHealth()
      .then(setHealth)
      .catch((error) => setError(errorMessage(error)));
  }
  async function newConversation() {
    const value = await createConversation();
    select(value.id);
    refreshList();
    return value.id;
  }
  async function act(action: () => Promise<unknown>) {
    try {
      await action();
      await refresh();
      refreshList();
      setError("");
    } catch (error) {
      setError(errorMessage(error));
    }
  }
  async function send(query: string, remote: boolean) {
    setSubmitting(true);
    try {
      const id = activeId || (await newConversation());
      await sendTurn(id, query, remote);
      await refresh(id);
      refreshList();
      setError("");
    } catch (error) {
      setError(errorMessage(error));
      throw error;
    } finally {
      setSubmitting(false);
    }
  }
  const pending = activeId
    ? sessionStorage.getItem(`edgedispatch-pending:${activeId}`)
    : null;
  return (
    <div className="app-shell">
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={select}
        onNew={() => void act(newConversation)}
        onArchive={(id) =>
          void act(async () => {
            await api(`/conversations/${id}`, "DELETE");
            if (id === activeId) setActiveId(null);
          })
        }
        onSettings={openSettings}
      />
      <div className="workspace">
        {(error || streamError) && (
          <div className="notice error" role="alert">
            {error || streamError}
            {pending && activeId && (
              <button onClick={() => void act(() => retryDelivery(activeId))}>
                Retry delivery
              </button>
            )}
            <button
              onClick={() =>
                void act(async () => {
                  setHealth(await getHealth());
                  await refresh();
                })
              }
            >
              Refresh status
            </button>
          </div>
        )}
        {health && health.status !== "ready" && (
          <div className="notice">
            Setup required: {!health?.redis ? "Redis is offline. " : ""}
            {!health?.artifacts_verified
              ? "Local model setup is incomplete - requests will use the remote model. "
              : !health?.local_model
                ? "Start llama.cpp to use the local model. "
                : ""}
            {!health?.remote_configured ? "Configure the remote API key. " : ""}
            {health?.remote_access_error
              ? `${health.remote_access_error} `
              : ""}
            <button onClick={openSettings}>View connections</button>
          </div>
        )}
        <button
          className="insights-toggle"
          aria-expanded={insightsOpen}
          aria-controls="conversation-insights"
          onClick={() => setInsightsOpen((value) => !value)}
        >
          {insightsOpen ? "Hide" : "Show"} conversation overview
        </button>
        <div className="workspace-columns">
          <ChatInterface
            conversation={conversation}
            loading={loading}
            submitting={submitting}
            disabled={
              !health?.redis || loading || Boolean(activeId && !conversation)
            }
            onSend={send}
            onCancel={() => {
              const turn = conversation?.turns.find(isActive);
              if (turn) void act(() => api(`/turns/${turn.id}/cancel`, "POST"));
            }}
            onPolicy={(policy: Policy) =>
              act(() =>
                api(`/conversations/${activeId}/settings`, "PUT", { policy }),
              )
            }
            onReset={() =>
              act(() =>
                api(`/conversations/${activeId}/reset-escalation`, "POST"),
              )
            }
            onApproval={(turn, action, approved) =>
              void act(() =>
                api(`/turns/${turn}/approvals/${action}`, "POST", { approved }),
              )
            }
            onReconcile={(turn) => {
              const note = prompt(
                "After checking the external system, describe the actual outcome. This does not retry the action.",
              );
              if (note)
                void act(() =>
                  api(`/turns/${turn}/reconcile`, "POST", { note }),
                );
            }}
          />
          <aside
            id="conversation-insights"
            className={`insights ${insightsOpen ? "open" : ""}`}
          >
            <div className="section-label">Conversation overview</div>
            <h2>Execution, at a glance.</h2>
            <div className="stat-grid">
              <div>
                <strong>{metrics?.completed_local ?? 0}</strong>
                <span>Completed locally</span>
              </div>
              <div>
                <strong>{metrics?.remote_used_turns ?? 0}</strong>
                <span>Escalated turns</span>
              </div>
            </div>
            <dl className="counts">
              <dt>Decision calls</dt>
              <dd>{metrics?.decision_calls ?? 0}</dd>
              <dt>Local execution calls</dt>
              <dd>{metrics?.local_execution_calls ?? 0}</dd>
              <dt>Remote API attempts</dt>
              <dd>{metrics?.remote_call_attempts ?? 0}</dd>
              <dt>MCP tool calls</dt>
              <dd>{metrics?.tool_calls ?? 0}</dd>
              <dt>Failed / cancelled</dt>
              <dd>{metrics?.failed_or_cancelled ?? 0}</dd>
              <dt>In progress</dt>
              <dd>{metrics?.pending ?? 0}</dd>
            </dl>
            <div className="cost-card">
              <span>Observed-usage API cost</span>
              <strong>{money(metrics?.known_api_cost_usd ?? "0")}</strong>
              {metrics && !metrics.api_cost_complete && (
                <small>
                  Partial total · {metrics.unknown_cost_attempts} attempt(s)
                  with unknown cost
                </small>
              )}
              <hr />
              <span>Estimated always-remote baseline</span>
              <b>{money(metrics?.estimated_always_remote_cost_usd)}</b>
              <span>Estimated API cost avoided</span>
              <b>{money(metrics?.estimated_cost_avoided_usd)}</b>
            </div>
            <details className="assumptions">
              <summary>What these numbers mean</summary>
              <p>
                API cost uses returned token usage and your saved price
                snapshot. Failed attempts may still be billable. The baseline
                estimates one prompt and the delivered answer at 4 characters
                per token; it omits hypothetical reasoning and tool loops. It is
                not measured savings. Local hardware and energy are unmeasured.
              </p>
            </details>
            <div className="connected-tools">
              <div className="section-label">Connected tools</div>
              {health?.mcp_servers.map((name) => (
                <span key={name}>● {name.replaceAll("_", " ")}</span>
              ))}
            </div>
            {localStorage.getItem("edgedispatch-conversations") && (
              <button
                onClick={() => {
                  const blob = new Blob(
                    [localStorage.getItem("edgedispatch-conversations")!],
                    { type: "application/json" },
                  );
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement("a");
                  a.href = url;
                  a.download = "previous-browser-history.json";
                  a.click();
                  URL.revokeObjectURL(url);
                }}
              >
                Download previous browser history
              </button>
            )}
          </aside>
        </div>
      </div>
      {settingsOpen && preferences && (
        <SettingsDialog
          preferences={preferences}
          health={health}
          onClose={() => setSettingsOpen(false)}
          onSave={async (value) => {
            const saved = preferencesSchema.parse(
              await api("/settings", "PUT", value),
            );
            setPreferences(saved);
            setSettingsOpen(false);
            try {
              setHealth(await getHealth());
            } catch (error) {
              setError(errorMessage(error));
            }
          }}
        />
      )}
    </div>
  );
}
