import { useEffect, useRef, useState } from "react";
import { errorMessage } from "@/lib/api";
import { isActive, policySchema } from "@/lib/types";
import type { Conversation, Policy } from "@/lib/types";
import ChatInput from "./ChatInput";
import MessageBubble from "./MessageBubble";

interface Props {
  conversation: Conversation | null;
  submitting: boolean;
  loading: boolean;
  disabled: boolean;
  onSend: (query: string, remote: boolean) => Promise<void>;
  onCancel: () => void;
  onPolicy: (policy: Policy) => Promise<void>;
  onReset: () => Promise<void>;
  onApproval: (turn: string, action: string, approved: boolean) => void;
  onReconcile: (turn: string) => void;
}
export default function ChatInterface(props: Props) {
  const { conversation } = props;
  const bottom = useRef<HTMLDivElement>(null);
  const [policyError, setPolicyError] = useState("");
  const [updatingPolicy, setUpdatingPolicy] = useState(false);
  const busy = props.submitting || Boolean(conversation?.turns.some(isActive));
  const staysRemote =
    conversation?.policy === "sticky_escalation" && conversation.sticky;
  const tail = conversation?.turns.at(-1);
  useEffect(() => {
    bottom.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "instant"
        : "smooth",
      block: "end",
    });
  }, [tail?.answer, tail?.state]);
  async function updatePolicy(action: () => Promise<void>) {
    if (busy || updatingPolicy) return;
    setUpdatingPolicy(true);
    try {
      await action();
      setPolicyError("");
    } catch (error) {
      setPolicyError(errorMessage(error));
    } finally {
      setUpdatingPolicy(false);
    }
  }
  return (
    <main className="chat-main">
      <header className="chat-header">
        <div>
          <h1>{conversation?.title || "Your local-first assistant"}</h1>
          <p>One request. The right execution path.</p>
        </div>
      </header>
      {conversation && (
        <div className="policy-bar">
          <label>
            Escalation policy{" "}
            <select
              value={conversation.policy}
              disabled={busy || updatingPolicy}
              onChange={(event) => {
                const policy = policySchema.parse(event.target.value);
                void updatePolicy(() => props.onPolicy(policy));
              }}
            >
              <option value="sticky_escalation">
                Stay remote after escalation
              </option>
              <option value="reconsider_each_turn">
                Classify every new request
              </option>
            </select>
          </label>
          {staysRemote && (
            <button
              disabled={busy || updatingPolicy}
              onClick={() => void updatePolicy(props.onReset)}
              title="Reconsider the next request without clearing history. It may still be escalated."
            >
              Classify again
            </button>
          )}
          <small aria-live="polite">
            {updatingPolicy
              ? "Saving policy…"
              : staysRemote
                ? "This chat stays remote until you choose Classify again."
                : conversation.policy === "reconsider_each_turn"
                  ? "Each new request will be classified."
                  : "Next request will be classified. Escalation stays within this chat."}
          </small>
        </div>
      )}
      {policyError && <p role="alert">{policyError}</p>}
      <div className="chat-scroll" aria-busy={props.loading}>
        {props.loading && <p role="status">Loading conversation…</p>}
        {!props.loading && !conversation?.turns.length && (
          <div className="welcome">
            <span className="welcome-eyebrow">YOUR WORK, CLOSER TO HOME</span>
            <h2>
              Start local.
              <br />
              Go further when needed.
            </h2>
            <p>
              Your assistant handles work locally when it can and escalates when
              needed, using the same connected tools and conversation.
            </p>
            <div className="examples">
              <span>Find Priya Shah’s annual leave balance</span>
              <span>What does the annual leave policy require?</span>
              <span>Compare the UK and US leave allowances</span>
            </div>
          </div>
        )}
        <div className="messages">
          {conversation?.turns.map((turn) => (
            <MessageBubble
              key={turn.id}
              turn={turn}
              onApproval={props.onApproval}
              onReconcile={props.onReconcile}
            />
          ))}
        </div>
        <div ref={bottom} />
      </div>
      <ChatInput
        key={conversation?.id || "new"}
        busy={busy}
        disabled={
          props.disabled ||
          updatingPolicy ||
          Boolean(
            conversation?.turns.some((t) => t.state === "needs_reconciliation"),
          )
        }
        onSend={props.onSend}
        onCancel={props.onCancel}
      />
    </main>
  );
}
