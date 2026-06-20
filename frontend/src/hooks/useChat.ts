import { useState, useCallback, useRef } from 'react'
import type { Message, Conversation } from '@/lib/types'
import { generateId } from '@/lib/utils'
import { streamChat, type ChatCallbacks } from '@/lib/api'

interface UseChatReturn {
  messages: Message[]
  isStreaming: boolean
  statusMessage: string
  sendMessage: (content: string, conversationId: string, threshold: number) => void
  cancelStream: () => void
  clearMessages: () => void
  loadMessages: (messages: Message[]) => void
}

export function useChat(): UseChatReturn {
  const [messages, setMessages] = useState<Message[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [statusMessage, setStatusMessage] = useState('')
  const abortRef = useRef<AbortController | null>(null)
  const assistantMsgIdRef = useRef<string>('')

  const cancelStream = useCallback(() => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setIsStreaming(false)
    setStatusMessage('')
  }, [])

  const sendMessage = useCallback(
    (content: string, conversationId: string, threshold: number) => {
      // Cancel any existing stream
      cancelStream()

      const userMsg: Message = {
        id: generateId(),
        role: 'user',
        content,
        timestamp: Date.now() / 1000,
      }

      const assistantMsgId = generateId()
      assistantMsgIdRef.current = assistantMsgId

      const assistantMsg: Message = {
        id: assistantMsgId,
        role: 'assistant',
        content: '',
        timestamp: Date.now() / 1000,
      }

      setMessages((prev) => [...prev, userMsg, assistantMsg])
      setIsStreaming(true)
      setStatusMessage('Analyzing query...')

      const callbacks: ChatCallbacks = {
        onToken: (text: string) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgIdRef.current
                ? { ...m, content: m.content + text }
                : m,
            ),
          )
        },
        onStatus: (msg: string) => {
          setStatusMessage(msg)
        },
        onDone: (meta) => {
          // Attach per-query metadata (cost, route, tool count) to the
          // assistant message so it can be rendered beneath the answer.
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgIdRef.current
                ? {
                    ...m,
                    cost: meta.cost,
                    wasEscalated: meta.wasEscalated,
                    toolCount: meta.toolCount,
                  }
                : m,
            ),
          )
          setIsStreaming(false)
          setStatusMessage('')
          abortRef.current = null
        },
        onError: (msg: string) => {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === assistantMsgIdRef.current
                ? { ...m, content: msg }
                : m,
            ),
          )
          setIsStreaming(false)
          setStatusMessage('')
          abortRef.current = null
        },
      }

      const controller = streamChat(content, conversationId, threshold, callbacks)
      abortRef.current = controller
    },
    [cancelStream],
  )

  const clearMessages = useCallback(() => {
    cancelStream()
    setMessages([])
  }, [cancelStream])

  const loadMessages = useCallback((msgs: Message[]) => {
    setMessages(msgs)
  }, [])

  return {
    messages,
    isStreaming,
    statusMessage,
    sendMessage,
    cancelStream,
    clearMessages,
    loadMessages,
  }
}
