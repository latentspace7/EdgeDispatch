import { Archive, MessageSquare, Plus, Settings2 } from "lucide-react";
import { useState } from "react";
import type { ConversationSummary } from "@/lib/types";

interface Props {
  conversations: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onArchive: (id: string) => void;
  onSettings: () => void;
}
export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onArchive,
  onSettings,
}: Props) {
  const [search, setSearch] = useState("");
  return (
    <aside className="sidebar">
      <div className="brand">
        <span className="brand-mark">E</span>
        <div>
          EdgeDispatch<small>Local-first workspace</small>
        </div>
      </div>
      <button className="primary new-chat" onClick={onNew}>
        <Plus size={16} /> New conversation
      </button>
      <label className="sr-only" htmlFor="search">
        Search conversations
      </label>
      <input
        id="search"
        className="search"
        placeholder="Search conversations"
        value={search}
        onChange={(event) => setSearch(event.target.value)}
      />
      <div className="section-label">Your conversations</div>
      <nav aria-label="Conversations">
        {conversations
          .filter((item) =>
            item.title.toLowerCase().includes(search.toLowerCase()),
          )
          .map((item) => (
            <div
              className={`thread ${item.id === activeId ? "selected" : ""}`}
              key={item.id}
            >
              <button
                aria-label={item.title}
                aria-current={item.id === activeId ? "page" : undefined}
                onClick={() => onSelect(item.id)}
              >
                <MessageSquare size={15} />
                <span>{item.title}</span>
              </button>
              <button
                className="icon-button archive"
                title="Archive conversation"
                aria-label={`Archive ${item.title}`}
                onClick={() => onArchive(item.id)}
              >
                <Archive size={14} />
              </button>
            </div>
          ))}
      </nav>
      <button className="settings-link" onClick={onSettings}>
        <Settings2 size={17} /> Settings & connections
      </button>
      <small className="sidebar-foot">
        Conversations saved on this machine
      </small>
    </aside>
  );
}
