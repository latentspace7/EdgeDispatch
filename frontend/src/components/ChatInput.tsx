import { useState, useRef, useEffect, type KeyboardEvent } from 'react'
import { Send, ZapOff } from 'lucide-react'

interface Props {
  onSend: (message: string) => void
  onCancel: () => void
  isStreaming: boolean
  disabled?: boolean
}

export default function ChatInput({ onSend, onCancel, isStreaming, disabled }: Props) {
  const [input, setInput] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + 'px'
    }
  }, [input])

  const handleSend = () => {
    const trimmed = input.trim()
    if (!trimmed || isStreaming || disabled) return
    onSend(trimmed)
    setInput('')
  }

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="border-t border-[#d4d1c8] bg-[#eceae3]/95 p-4">
      <div className="max-w-3xl mx-auto">
        <div className="relative flex items-end gap-2 bg-white border border-[#cbc7bd] rounded-xl p-2 transition-all duration-300 focus-within:border-[#e2231a]/70 focus-within:shadow-neon-cyan">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask anything..."
            rows={1}
            disabled={disabled}
            className="flex-1 bg-transparent text-sm text-[#242424] placeholder-[#77736a] resize-none outline-none px-3 py-2 max-h-40 disabled:opacity-50"
          />

          <button
            onClick={isStreaming ? onCancel : handleSend}
            disabled={!input.trim() && !isStreaming}
            className={`
              flex-shrink-0 w-10 h-10 rounded-xl flex items-center justify-center
              transition-all duration-300
              ${isStreaming
                ? 'bg-edge-rose/20 text-edge-rose border border-edge-rose/30 hover:bg-edge-rose/30'
                : 'bg-[#e2231a] text-white border border-[#b41414] hover:bg-[#b41414] hover:shadow-neon-cyan disabled:opacity-30 disabled:hover:shadow-none'
              }
            `}
          >
            {isStreaming ? (
              <ZapOff className="w-4 h-4" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </button>
        </div>

        <p className="text-[10px] text-[#6d6a62] text-center mt-2">
          EdgeDispatch - hybrid local + cloud inference
        </p>
      </div>
    </div>
  )
}
