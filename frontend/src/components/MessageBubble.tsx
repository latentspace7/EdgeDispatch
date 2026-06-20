import type { Message, CostBreakdown } from '@/lib/types'
import { Bot, User } from 'lucide-react'

interface Props {
  message: Message
}

function formatCost(usd: number): string {
  if (usd <= 0) return '$0.00'
  if (usd < 0.0001) return `$${usd.toExponential(2)}`
  if (usd < 0.01) return `$${usd.toFixed(5)}`
  if (usd < 1) return `$${usd.toFixed(4)}`
  return `$${usd.toFixed(2)}`
}

function CostBadge({ cost, wasEscalated }: { cost: CostBreakdown; wasEscalated: boolean }) {
  if (wasEscalated) {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-2 pt-2 border-t border-cyber-border/60">
        <span className="text-[10px] text-edge-violet/90 font-medium">☁ Cloud synthesis</span>
        <span className="text-[10px] text-slate-500">
          {cost.tokens_ed_in.toLocaleString()} tok to cloud
        </span>
        <span className="text-[10px] text-edge-violet font-mono">
          {formatCost(cost.c_ed)}
        </span>
        <span className="text-[10px] text-slate-600">
          vs monolithic {formatCost(cost.c_mono)}
        </span>
        <span className="text-[10px] text-edge-emerald font-mono">
          saved {formatCost(cost.delta_c)}
        </span>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-2 pt-2 border-t border-cyber-border/60">
      <span className="text-[10px] text-edge-cyan/90 font-medium">⌂ Local resolution</span>
      <span className="text-[10px] text-slate-500">0 tok to cloud</span>
      <span className="text-[10px] text-edge-cyan font-mono">
        {formatCost(cost.c_ed)}
      </span>
      <span className="text-[10px] text-slate-600">
        vs monolithic {formatCost(cost.c_mono)}
      </span>
      <span className="text-[10px] text-edge-emerald font-mono">
        saved {formatCost(cost.delta_c)}
      </span>
    </div>
  )
}

export default function MessageBubble({ message }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'} group`}>
      {/* Avatar (assistant only) */}
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-gradient-to-br from-edge-cyan/20 to-edge-violet/20 border border-cyber-border flex items-center justify-center mt-0.5">
          <Bot className="w-4 h-4 text-edge-cyan" />
        </div>
      )}

      <div
        className={`
          max-w-[75%] rounded-2xl px-4 py-3
          ${isUser
            ? 'bg-gradient-to-br from-edge-violet/20 to-edge-violet/10 border border-edge-violet/20 text-white'
            : 'bg-cyber-card border border-cyber-border text-slate-200'
          }
        `}
      >
        {/* Content */}
        <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
          {message.content || (
            <span className="text-slate-500 italic">Thinking...</span>
          )}
        </div>

        {/* Cost / route metadata (assistant only, when present) */}
        {!isUser && message.cost && (
          <CostBadge cost={message.cost} wasEscalated={Boolean(message.wasEscalated)} />
        )}

        {/* Timestamp */}
        <div className={`text-[10px] mt-1.5 ${isUser ? 'text-edge-violet/50' : 'text-slate-600'}`}>
          {new Date(message.timestamp * 1000).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      </div>

      {/* Avatar (user only) */}
      {isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-gradient-to-br from-edge-violet/20 to-edge-cyan/20 border border-edge-violet/20 flex items-center justify-center mt-0.5">
          <User className="w-4 h-4 text-edge-violet" />
        </div>
      )}
    </div>
  )
}
