import { useEffect, useRef } from 'react'
import type { Message, Conversation } from '@/lib/types'
import MessageBubble from './MessageBubble'
import ChatInput from './ChatInput'
import { Settings } from 'lucide-react'

interface Props {
  conversation: Conversation | null
  messages: Message[]
  isStreaming: boolean
  statusMessage: string
  threshold: number
  showHandoffDetails: boolean
  onSend: (message: string) => void
  onCancel: () => void
  onSettings: () => void
}

export default function ChatInterface({
  conversation,
  messages,
  isStreaming,
  statusMessage,
  threshold,
  showHandoffDetails,
  onSend,
  onCancel,
  onSettings,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages, statusMessage])

  return (
    <div className="flex-1 flex flex-col h-full bg-cyber-bg">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-4 border-b border-[#242424] bg-[#242424] text-white">
        <div className="flex items-center gap-3 min-w-0">
          <div className="w-8 h-8 rounded-md bg-[#e2231a] flex items-center justify-center shadow-[0_10px_24px_rgba(226,35,26,0.28)]">
            <span className="text-[11px] font-bold text-white">E</span>
          </div>
          <div className="min-w-0">
            <h1 className="text-sm font-semibold text-white truncate">
              {conversation?.title || 'EdgeDispatch'}
            </h1>
            <div className="flex items-center gap-2">
              <span className="text-[10px] text-white/62">
                Threshold: {threshold} tools
              </span>
              {statusMessage && (
                <>
                  <span className="text-white/35">·</span>
                  <span className="text-[10px] text-[#ff7a73] animate-pulse">
                    {statusMessage}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>

        <button
          onClick={onSettings}
          className="w-8 h-8 rounded-lg flex items-center justify-center text-white/70 hover:text-white hover:bg-white/10 transition-colors"
        >
          <Settings className="w-4 h-4" />
        </button>
      </div>

      {/* Messages */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto overscroll-contain bg-[#f3f2ec]"
      >
        {messages.length === 0 ? (
          /* Empty state */
          <div className="flex flex-col items-center justify-center h-full px-6 text-center">
            <div className="w-16 h-16 rounded-lg bg-[#e2231a] border border-[#b41414] flex items-center justify-center mb-6 animate-glow-cyan">
              <span className="text-2xl font-bold text-white">
                E
              </span>
            </div>
            <h2 className="text-lg font-semibold text-[#242424] mb-2">
              EdgeDispatch
            </h2>
            <p className="text-sm text-[#424242] max-w-sm">
              Hybrid LLM orchestration with local-first AI workflows.
              Simple queries run locally. Complex tasks escalate to the cloud.
            </p>
            <div className="flex gap-3 mt-6">
              <div className="px-3 py-1.5 rounded-md border border-[#242424]/20 bg-white text-[11px] text-[#424242]">
                <span className="text-edge-cyan">●</span> Local inference
              </div>
              <div className="px-3 py-1.5 rounded-md border border-[#242424]/20 bg-white text-[11px] text-[#424242]">
                <span className="text-edge-violet">●</span> Cloud synthesis
              </div>
            </div>
          </div>
        ) : (
          <div className="max-w-3xl mx-auto px-4 py-6 space-y-4">
            {messages.map((msg) => (
              <MessageBubble
                key={msg.id}
                message={msg}
                showHandoffDetails={showHandoffDetails}
              />
            ))}

            {/* Streaming indicator */}
            {isStreaming && (
              <div className="flex items-center gap-2 px-4 py-2">
                <div className="flex gap-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-edge-cyan animate-pulse" />
                  <span className="w-1.5 h-1.5 rounded-full bg-edge-cyan animate-pulse" style={{ animationDelay: '0.15s' }} />
                  <span className="w-1.5 h-1.5 rounded-full bg-edge-cyan animate-pulse" style={{ animationDelay: '0.3s' }} />
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput
        onSend={onSend}
        onCancel={onCancel}
        isStreaming={isStreaming}
      />
    </div>
  )
}
