import { useState } from "react";
import { ArrowUp, Square } from "lucide-react";

interface Props {
  busy: boolean;
  disabled: boolean;
  onSend: (query: string, remote: boolean) => Promise<void>;
  onCancel: () => void;
}
export default function ChatInput({ busy, disabled, onSend, onCancel }: Props) {
  const [query, setQuery] = useState("");
  const [remote, setRemote] = useState(false);
  const [sending, setSending] = useState(false);
  async function submit() {
    if (!query.trim() || busy || sending || disabled) return;
    setSending(true);
    try {
      await onSend(query, remote);
      setQuery("");
      setRemote(false);
    } catch {
      return;
    } finally {
      setSending(false);
    }
  }
  return (
    <div className="composer-wrap">
      <div className="composer">
        <label className="sr-only" htmlFor="message">
          Message
        </label>
        <textarea
          id="message"
          disabled={sending || disabled}
          rows={3}
          placeholder="Ask about an employee, a document, or a policy…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer-actions">
          <label>
            <input
              type="checkbox"
              disabled={sending || disabled}
              checked={remote}
              onChange={(event) => setRemote(event.target.checked)}
            />{" "}
            Escalate directly
          </label>
          {busy ? (
            <button className="stop" onClick={onCancel}>
              <Square size={13} /> Stop
            </button>
          ) : (
            <button
              className="primary"
              aria-label="Send message"
              disabled={disabled || sending || !query.trim()}
              onClick={() => void submit()}
            >
              <ArrowUp size={18} />
            </button>
          )}
        </div>
      </div>
      <small>
        Enter to send · Shift + Enter for a new line · Verify important answers
      </small>
    </div>
  );
}
