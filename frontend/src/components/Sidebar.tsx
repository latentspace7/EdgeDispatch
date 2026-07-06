import { useState } from 'react'
import { Plus, Trash2, MessageSquare, PanelLeftClose, PanelLeft, Settings } from 'lucide-react'
import type { Conversation } from '@/lib/types'
import { cn, formatTimestamp, truncate } from '@/lib/utils'

interface Props {
  conversations: Conversation[]
  activeId: string | null
  onSelect: (conv: Conversation) => void
  onNew: () => void
  onDelete: (convId: string) => void
  onSettings: () => void
}

export default function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
  onSettings,
}: Props) {
  const [collapsed, setCollapsed] = useState(false)
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  return (
    <div
      className={cn(
        'h-full flex flex-col border-r border-[#242424] bg-[#242424] text-white shadow-[8px_0_28px_rgba(0,0,0,0.14)] transition-all duration-300',
        collapsed ? 'w-[52px]' : 'w-[280px]',
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-white/10">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-md bg-[#e2231a] flex items-center justify-center shadow-[0_10px_24px_rgba(226,35,26,0.22)]">
              <span className="text-[10px] font-bold text-white">E</span>
            </div>
            <span className="text-sm font-semibold text-white tracking-wide">EdgeDispatch</span>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-white/68 hover:text-white hover:bg-white/10 transition-colors"
        >
          {collapsed ? <PanelLeft className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
        </button>
      </div>

      {/* New chat button */}
      <div className="p-3">
        <button
          onClick={onNew}
          className={cn(
            'w-full flex items-center gap-2 rounded-xl transition-all duration-300',
            collapsed
              ? 'justify-center p-2 border border-white/15 text-white/70 hover:text-white hover:border-[#e2231a]/70'
              : 'px-3 py-2.5 border border-white/15 text-white/82 hover:text-white hover:border-[#e2231a]/70 hover:bg-white/8 hover:shadow-[0_10px_24px_rgba(226,35,26,0.14)]',
          )}
        >
          <Plus className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span className="text-xs">New conversation</span>}
        </button>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2">
        {conversations.length === 0 && !collapsed && (
          <p className="text-xs text-white/52 text-center mt-8 px-4">
            No conversations yet. Start a new one.
          </p>
        )}

        {conversations.map((conv) => (
          <div
            key={conv.id}
            className={cn(
              'group relative flex items-center rounded-xl mb-0.5 transition-all duration-200 cursor-pointer',
              collapsed ? 'justify-center p-2' : 'px-3 py-2.5',
              activeId === conv.id
                ? 'bg-white/12 border border-[#e2231a]/70 shadow-[0_8px_24px_rgba(0,0,0,0.16)]'
                : 'hover:bg-white/8 border border-transparent',
            )}
            onClick={() => onSelect(conv)}
            onMouseEnter={() => setHoveredId(conv.id)}
            onMouseLeave={() => setHoveredId(null)}
          >
            {collapsed ? (
              <MessageSquare className="w-4 h-4 text-white/62" />
            ) : (
              <>
                <MessageSquare
                  className={cn(
                    'w-4 h-4 flex-shrink-0 mr-2.5',
                    activeId === conv.id ? 'text-[#ff5a52]' : 'text-white/62',
                  )}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-white truncate">{truncate(conv.title, 28)}</p>
                  <p className="text-[10px] text-white/46 mt-0.5">
                    {formatTimestamp(conv.updatedAt)}
                  </p>
                </div>

                {/* Delete button */}
                {hoveredId === conv.id && (
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      onDelete(conv.id)
                    }}
                    className="flex-shrink-0 ml-2 w-6 h-6 rounded-lg flex items-center justify-center text-white/52 hover:text-[#ff7a73] hover:bg-white/10 transition-colors"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </>
            )}
          </div>
        ))}
      </div>

      {/* Footer */}
      <div className="p-3 border-t border-white/10">
        <button
          onClick={onSettings}
          className={cn(
            'w-full flex items-center rounded-xl transition-all duration-300 text-xs',
            collapsed
              ? 'justify-center p-2 border border-white/15 text-white/68 hover:text-white'
              : 'px-3 py-2 gap-2 text-white/68 hover:text-white hover:bg-white/8',
          )}
        >
          <Settings className="h-4 w-4 flex-shrink-0" />
          {!collapsed && 'Settings'}
        </button>
      </div>
    </div>
  )
}
