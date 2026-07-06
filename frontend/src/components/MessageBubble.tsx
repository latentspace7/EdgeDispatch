import { useState } from 'react'
import type { Message, CostBreakdown, HandoffDetails } from '@/lib/types'
import { Bot, ChevronDown, Cloud, Cpu, FileText, User } from 'lucide-react'
import MarkdownMessage from './MarkdownMessage'

interface Props {
  message: Message
  showHandoffDetails: boolean
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
      <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-2 pt-2 border-t border-[#d4d1c8]">
        <span className="inline-flex items-center gap-1 text-[10px] text-edge-violet font-semibold">
          <Cloud className="h-3 w-3" />
          Cloud synthesis
        </span>
        <span className="text-[10px] text-slate-600">
          {cost.tokens_ed_in.toLocaleString()} tok to cloud
        </span>
        <span className="text-[10px] text-edge-violet font-mono">
          {formatCost(cost.c_ed)}
        </span>
        <span className="text-[10px] text-slate-500">
          vs monolithic {formatCost(cost.c_mono)}
        </span>
        <span className="text-[10px] text-edge-emerald font-mono">
          saved {formatCost(cost.delta_c)}
        </span>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5 mt-2 pt-2 border-t border-[#d4d1c8]">
      <span className="inline-flex items-center gap-1 text-[10px] text-edge-cyan font-semibold">
        <Cpu className="h-3 w-3" />
        Local resolution
      </span>
      <span className="text-[10px] text-slate-600">0 tok to cloud</span>
      <span className="text-[10px] text-edge-cyan font-mono">
        {formatCost(cost.c_ed)}
      </span>
      <span className="text-[10px] text-slate-500">
        vs monolithic {formatCost(cost.c_mono)}
      </span>
      <span className="text-[10px] text-edge-emerald font-mono">
        saved {formatCost(cost.delta_c)}
      </span>
    </div>
  )
}

function HandoffPanel({ handoff }: { handoff: HandoffDetails }) {
  const [open, setOpen] = useState(false)
  const evidenceText = JSON.stringify(handoff.evidence, null, 2)

  return (
    <div className="mt-2 pt-2 border-t border-[#d4d1c8]">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[11px] text-slate-600 hover:bg-cyber-surface/45 hover:text-slate-950 transition-colors"
      >
        <span className="inline-flex min-w-0 items-center gap-1.5">
          <FileText className="h-3.5 w-3.5 text-edge-cyan" />
          <span className="font-medium">Handoff</span>
          <span className="truncate text-slate-500">
            {handoff.selectedTools.length} tool{handoff.selectedTools.length === 1 ? '' : 's'}
          </span>
        </span>
        <ChevronDown
          className={`h-3.5 w-3.5 flex-shrink-0 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="mt-2 space-y-2 rounded-lg border border-[#d4d1c8] bg-[#f8f7f3] p-3">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-slate-600">Tools invoked</p>
            <div className="mt-1 flex flex-wrap gap-1.5">
              {handoff.selectedTools.length > 0 ? (
                handoff.selectedTools.map((tool) => (
                  <span
                    key={tool}
                    className="rounded-md border border-[#e2231a]/30 bg-[#e2231a]/10 px-1.5 py-0.5 font-mono text-[10px] text-[#242424]"
                  >
                    {tool}
                  </span>
                ))
              ) : (
                <span className="text-[11px] text-slate-600">No tools recorded</span>
              )}
            </div>
          </div>

          <div>
            <p className="text-[10px] uppercase tracking-wider text-slate-600">Rationale</p>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-800">
              {handoff.rationale || 'No rationale recorded.'}
            </p>
          </div>

          <details className="group">
            <summary className="cursor-pointer list-none text-[10px] uppercase tracking-wider text-slate-600 group-open:text-slate-800">
              Evidence
            </summary>
            <pre className="mt-1 max-h-56 overflow-auto rounded-md border border-[#d4d1c8] bg-white p-2 text-[10px] leading-relaxed text-[#242424]">
              {evidenceText}
            </pre>
          </details>

          <details className="group">
            <summary className="cursor-pointer list-none text-[10px] uppercase tracking-wider text-slate-600 group-open:text-slate-800">
              Prompt sent to cloud
            </summary>
            <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-[#d4d1c8] bg-white p-2 text-[10px] leading-relaxed text-[#242424]">
              {handoff.prompt}
            </pre>
          </details>
        </div>
      )}
    </div>
  )
}

export default function MessageBubble({ message, showHandoffDetails }: Props) {
  const isUser = message.role === 'user'

  return (
    <div className={`flex gap-3 ${isUser ? 'justify-end' : 'justify-start'} group`}>
      {/* Avatar (assistant only) */}
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-[#e2231a] border border-[#b41414] flex items-center justify-center mt-0.5 shadow-neon-cyan">
          <Bot className="w-4 h-4 text-white" />
        </div>
      )}

      <div
        className={`
          max-w-[75%] rounded-xl px-4 py-3
          ${isUser
            ? 'bg-white border border-[#242424]/28 text-[#242424] shadow-[0_8px_22px_rgba(36,36,36,0.08)]'
            : 'bg-white border border-[#242424]/16 text-[#242424] shadow-[0_8px_22px_rgba(36,36,36,0.07)]'
          }
        `}
      >
        {/* Content */}
        <div className="text-sm leading-relaxed break-words">
          {message.content ? (
            <MarkdownMessage content={message.content} isUser={isUser} />
          ) : (
            <span className="text-slate-600 italic">Thinking...</span>
          )}
        </div>

        {/* Cost / route metadata (assistant only, when present) */}
        {!isUser && message.cost && (
          <CostBadge cost={message.cost} wasEscalated={Boolean(message.wasEscalated)} />
        )}

        {!isUser && showHandoffDetails && message.handoff && (
          <HandoffPanel handoff={message.handoff} />
        )}

        {/* Timestamp */}
        <div className="text-[10px] mt-1.5 text-slate-500">
          {new Date(message.timestamp * 1000).toLocaleTimeString([], {
            hour: '2-digit',
            minute: '2-digit',
          })}
        </div>
      </div>

      {/* Avatar (user only) */}
      {isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-full bg-[#242424] border border-[#242424] flex items-center justify-center mt-0.5 shadow-[0_8px_22px_rgba(36,36,36,0.12)]">
          <User className="w-4 h-4 text-white" />
        </div>
      )}
    </div>
  )
}
