import { useState } from 'react'
import { Plus, Trash2, MessageSquare, PanelLeftClose, PanelLeft } from 'lucide-react'
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
        'h-full flex flex-col border-r border-cyber-border transition-all duration-300 glass',
        collapsed ? 'w-[52px]' : 'w-[280px]',
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-cyber-border">
        {!collapsed && (
          <div className="flex items-center gap-2">
            <div className="w-6 h-6 rounded-md bg-gradient-to-br from-edge-cyan to-edge-violet flex items-center justify-center">
              <span className="text-[10px] font-bold text-white">E</span>
            </div>
            <span className="text-sm font-semibold text-white tracking-wide">EdgeDispatch</span>
          </div>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-slate-500 hover:text-white hover:bg-cyber-card transition-colors"
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
              ? 'justify-center p-2 border border-cyber-border text-slate-400 hover:text-edge-cyan hover:border-edge-cyan/30'
              : 'px-3 py-2.5 border border-cyber-border text-slate-400 hover:text-edge-cyan hover:border-edge-cyan/30 hover:shadow-neon-cyan',
          )}
        >
          <Plus className="w-4 h-4 flex-shrink-0" />
          {!collapsed && <span className="text-xs">New conversation</span>}
        </button>
      </div>

      {/* Conversation list */}
      <div className="flex-1 overflow-y-auto px-2">
        {conversations.length === 0 && !collapsed && (
          <p className="text-xs text-slate-600 text-center mt-8 px-4">
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
                ? 'bg-gradient-to-r from-edge-violet/10 to-edge-cyan/5 border border-edge-violet/20'
                : 'hover:bg-cyber-card border border-transparent',
            )}
            onClick={() => onSelect(conv)}
            onMouseEnter={() => setHoveredId(conv.id)}
            onMouseLeave={() => setHoveredId(null)}
          >
            {collapsed ? (
              <MessageSquare className="w-4 h-4 text-slate-500" />
            ) : (
              <>
                <MessageSquare
                  className={cn(
                    'w-4 h-4 flex-shrink-0 mr-2.5',
                    activeId === conv.id ? 'text-edge-violet' : 'text-slate-600',
                  )}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-slate-300 truncate">{truncate(conv.title, 28)}</p>
                  <p className="text-[10px] text-slate-600 mt-0.5">
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
                    className="flex-shrink-0 ml-2 w-6 h-6 rounded-lg flex items-center justify-center text-slate-600 hover:text-edge-rose hover:bg-edge-rose/10 transition-colors"
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
      <div className="p-3 border-t border-cyber-border">
        <button
          onClick={onSettings}
          className={cn(
            'w-full flex items-center rounded-xl transition-all duration-300 text-xs',
            collapsed
              ? 'justify-center p-2 border border-cyber-border text-slate-500 hover:text-white'
              : 'px-3 py-2 gap-2 text-slate-500 hover:text-white hover:bg-cyber-card',
          )}
        >
          <span className="text-sm">⚙</span>
          {!collapsed && 'Settings'}
        </button>
      </div>
    </div>
  )
}
