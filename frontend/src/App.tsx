import { useState, useCallback, useEffect } from 'react'
import type { Conversation, Message } from '@/lib/types'
import { generateId } from '@/lib/utils'
import { useLocalStorage } from '@/hooks/useLocalStorage'
import { useChat } from '@/hooks/useChat'
import Sidebar from '@/components/Sidebar'
import ChatInterface from '@/components/ChatInterface'
import SettingsDialog from '@/components/SettingsDialog'

export default function App() {
  // Persisted state
  const [conversations, setConversations] = useLocalStorage<Conversation[]>(
    'edgedispatch-conversations',
    [],
  )
  const [activeId, setActiveId] = useLocalStorage<string | null>(
    'edgedispatch-active-id',
    null,
  )
  const [threshold, setThreshold] = useLocalStorage<number>(
    'edgedispatch-threshold',
    2,
  )
  const [priceInput, setPriceInput] = useLocalStorage<number>(
    'edgedispatch-price-input',
    5,
  )
  const [priceOutput, setPriceOutput] = useLocalStorage<number>(
    'edgedispatch-price-output',
    15,
  )

  // UI state
  const [settingsOpen, setSettingsOpen] = useState(false)

  // Chat hook
  const {
    messages,
    isStreaming,
    statusMessage,
    sendMessage,
    cancelStream,
    clearMessages,
    loadMessages,
  } = useChat()

  // Get active conversation
  const activeConv = conversations.find((c) => c.id === activeId) || null

  // Sync messages to conversations when they change
  useEffect(() => {
    if (!activeId || messages.length === 0) return

    setConversations((prev) =>
      prev.map((c) =>
        c.id === activeId
          ? { ...c, messages, updatedAt: Date.now() / 1000 }
          : c,
      ),
    )
  }, [messages, activeId])

  const handleNewConversation = useCallback(() => {
    cancelStream()
    clearMessages()

    const newConv: Conversation = {
      id: generateId(),
      title: 'New conversation',
      messages: [],
      createdAt: Date.now() / 1000,
      updatedAt: Date.now() / 1000,
    }

    setConversations((prev) => [newConv, ...prev])
    setActiveId(newConv.id)
  }, [cancelStream, clearMessages, setConversations, setActiveId])

  const handleSelectConversation = useCallback(
    (conv: Conversation) => {
      cancelStream()
      setActiveId(conv.id)
      loadMessages(conv.messages)
    },
    [cancelStream, setActiveId, loadMessages],
  )

  const handleDeleteConversation = useCallback(
    (convId: string) => {
      cancelStream()

      setConversations((prev) => {
        const filtered = prev.filter((c) => c.id !== convId)

        // If deleting active conversation, switch to most recent or clear
        if (convId === activeId) {
          if (filtered.length > 0) {
            const next = filtered[0]
            setActiveId(next.id)
            loadMessages(next.messages)
          } else {
            setActiveId(null)
            clearMessages()
          }
        }

        return filtered
      })
    },
    [activeId, cancelStream, clearMessages, loadMessages, setActiveId, setConversations],
  )

  const handleSend = useCallback(
    (content: string) => {
      // Auto-create conversation if none active
      let convId = activeId
      if (!convId) {
        const newConv: Conversation = {
          id: generateId(),
          title: content.slice(0, 60),
          messages: [],
          createdAt: Date.now() / 1000,
          updatedAt: Date.now() / 1000,
        }
        setConversations((prev) => [newConv, ...prev])
        setActiveId(newConv.id)
        convId = newConv.id
      }

      sendMessage(content, convId, threshold)
    },
    [activeId, threshold, sendMessage, setConversations, setActiveId],
  )

  const handleThresholdChange = useCallback(
    (t: number) => {
      setThreshold(t)
    },
    [setThreshold],
  )

  const handlePricingChange = useCallback(
    (inputPerMTok: number, outputPerMTok: number) => {
      setPriceInput(inputPerMTok)
      setPriceOutput(outputPerMTok)
    },
    [setPriceInput, setPriceOutput],
  )

  return (
    <div className="h-screen flex bg-cyber-bg text-slate-200 antialiased">
      {/* Background grid */}
      <div
        className="fixed inset-0 opacity-[0.03] pointer-events-none"
        style={{
          backgroundImage:
            'linear-gradient(rgba(0,240,255,0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(0,240,255,0.3) 1px, transparent 1px)',
          backgroundSize: '60px 60px',
        }}
      />

      {/* Sidebar */}
      <Sidebar
        conversations={conversations}
        activeId={activeId}
        onSelect={handleSelectConversation}
        onNew={handleNewConversation}
        onDelete={handleDeleteConversation}
        onSettings={() => setSettingsOpen(true)}
      />

      {/* Main chat area */}
      <div className="flex-1 flex flex-col relative z-10">
        <ChatInterface
          conversation={activeConv}
          messages={messages}
          isStreaming={isStreaming}
          statusMessage={statusMessage}
          threshold={threshold}
          onSend={handleSend}
          onCancel={cancelStream}
          onSettings={() => setSettingsOpen(true)}
        />
      </div>

      {/* Settings dialog */}
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        threshold={threshold}
        onThresholdChange={handleThresholdChange}
        onPricingChange={handlePricingChange}
      />
    </div>
  )
}
