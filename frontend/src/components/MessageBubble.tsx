import type { Turn } from "@/lib/types";
import MarkdownMessage from "./MarkdownMessage";

interface Props {
  turn: Turn;
  onApproval: (turn: string, action: string, approved: boolean) => void;
  onReconcile: (turn: string) => void;
}
export default function MessageBubble({
  turn,
  onApproval,
  onReconcile,
}: Props) {
  const route =
    turn.executor === "ESCALATE"
      ? turn.decision?.route === "LOCAL"
        ? "LOCAL → REMOTE"
        : "ESCALATED"
      : turn.executor === "LOCAL"
        ? "LOCAL"
        : turn.decision
          ? `${turn.decision.route} selected`
          : ["queued", "deciding"].includes(turn.state)
            ? "DECIDING"
            : "NOT EXECUTED";
  return (
    <article className="turn">
      <div className="user-message">
        <div className="message-author">You</div>
        <MarkdownMessage content={turn.query} />
      </div>
      <div className="assistant-message">
        <div className="message-author">
          <span className="avatar">E</span> EdgeDispatch{" "}
          <span
            className={`badge ${turn.executor === "LOCAL" ? "local" : "remote"}`}
          >
            {route}
          </span>
          <span className="turn-status">{turn.state.replaceAll("_", " ")}</span>
        </div>
        {turn.answer ? (
          <MarkdownMessage content={turn.answer} />
        ) : (
          <p className="muted">
            {turn.error ||
              turn.reason?.replaceAll("_", " ") ||
              "Preparing your answer…"}
          </p>
        )}
        {turn.error && turn.answer && (
          <p className="error-text">{turn.error}</p>
        )}
        {turn.decision && (
          <small className="decision-reason">
            Decision: {turn.decision.reason.replaceAll("_", " ")}
            {turn.latency_ms !== undefined
              ? ` · ${(turn.latency_ms / 1000).toFixed(1)}s`
              : ""}
          </small>
        )}
        {turn.approvals.map((approval) => (
          <div className="approval" key={approval.action_id}>
            <strong>Permission to run {approval.tool}?</strong>
            <p>This tool may change data or contact an external service.</p>
            <pre>{JSON.stringify(approval.arguments, null, 2)}</pre>
            <button
              className="primary"
              onClick={() => onApproval(turn.id, approval.action_id, true)}
            >
              Approve once
            </button>
            <button
              onClick={() => onApproval(turn.id, approval.action_id, false)}
            >
              Decline
            </button>
          </div>
        ))}
        {turn.state === "needs_reconciliation" && (
          <div className="approval">
            <p>
              An external action has an uncertain result. Check the target
              system before continuing.
            </p>
            <button onClick={() => onReconcile(turn.id)}>
              Record verified outcome
            </button>
          </div>
        )}
      </div>
    </article>
  );
}
