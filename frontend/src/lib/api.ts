import type { Settings, StreamDone, CostBreakdown, HandoffDetails } from './types'

const API_BASE = '/api'
const SSE_LINE_SEPARATOR = '\r\n'
const SSE_EVENT_DELIMITER = `${SSE_LINE_SEPARATOR}${SSE_LINE_SEPARATOR}`

// Backend SettingsResponse uses snake_case; the frontend Settings type uses
// camelCase. This maps one to the other.
function mapSettings(raw: Record<string, unknown>): Settings {
  return {
    toolThreshold: Number(raw.tool_threshold ?? 2),
    priceInputPerMTok: Number(raw.price_input_per_mtok ?? 5),
    priceOutputPerMTok: Number(raw.price_output_per_mtok ?? 15),
    localModel: String(raw.local_model ?? ''),
    highEndModel: String(raw.high_end_model ?? ''),
    mcpServerCount: Number(raw.mcp_server_count ?? 0),
    arizeEndpoint: String(raw.arize_endpoint ?? ''),
  }
}

export async function fetchSettings(): Promise<Settings> {
  const res = await fetch(`${API_BASE}/settings`)
  if (!res.ok) throw new Error(`Settings fetch failed: ${res.status}`)
  return mapSettings(await res.json())
}

export interface PricingUpdate {
  threshold?: number
  priceInputPerMTok?: number
  priceOutputPerMTok?: number
}

export async function updateSettings(update: PricingUpdate): Promise<Settings> {
  const body: Record<string, unknown> = {}
  if (update.threshold !== undefined) body.tool_threshold = update.threshold
  if (update.priceInputPerMTok !== undefined) body.price_input_per_mtok = update.priceInputPerMTok
  if (update.priceOutputPerMTok !== undefined) body.price_output_per_mtok = update.priceOutputPerMTok

  const res = await fetch(`${API_BASE}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`Settings update failed: ${res.status}`)
  return mapSettings(await res.json())
}

export async function deleteConversation(convId: string): Promise<void> {
  const res = await fetch(`${API_BASE}/conversations/${convId}`, {
    method: 'DELETE',
  })
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`)
}

export interface ChatCallbacks {
  onToken: (text: string) => void
  onStatus: (message: string) => void
  onDone: (meta: StreamDone) => void
  onError: (message: string) => void
}

function mapDone(parsed: Record<string, unknown>): StreamDone {
  const rawHandoff = parsed.handoff as Record<string, unknown> | undefined
  const handoff: HandoffDetails | undefined = rawHandoff
    ? {
        query: String(rawHandoff.query ?? ''),
        selectedTools: Array.isArray(rawHandoff.selected_tools)
          ? rawHandoff.selected_tools.map(String)
          : [],
        rationale: String(rawHandoff.rationale ?? ''),
        evidence: Array.isArray(rawHandoff.evidence) ? rawHandoff.evidence : [],
        toolThreshold: Number(rawHandoff.tool_threshold ?? parsed.threshold ?? 2),
        prompt: String(rawHandoff.prompt ?? ''),
      }
    : undefined

  return {
    conversationId: String(parsed.conversation_id ?? ''),
    wasEscalated: Boolean(parsed.was_escalated ?? false),
    toolCount: Number(parsed.tool_count ?? 0),
    threshold: Number(parsed.threshold ?? 2),
    cost: parsed.cost as CostBreakdown | undefined,
    evaluation: parsed.evaluation as Record<string, unknown> | undefined,
    handoff,
  }
}

function parseSseFieldValue(line: string, fieldName: string): string | null {
  if (!line.startsWith(`${fieldName}:`)) return null
  const value = line.slice(fieldName.length + 1)
  return value.startsWith(' ') ? value.slice(1) : value
}

function dispatchSseEvent(eventBlock: string, callbacks: ChatCallbacks): void {
  if (!eventBlock.trim()) return

  const lines = eventBlock.split(SSE_LINE_SEPARATOR)
  let eventType = ''
  const dataLines: string[] = []

  for (const line of lines) {
    const eventValue = parseSseFieldValue(line, 'event')
    if (eventValue !== null) {
      eventType = eventValue.trim()
      continue
    }

    const dataValue = parseSseFieldValue(line, 'data')
    if (dataValue !== null) {
      dataLines.push(dataValue)
    }
  }

  const eventData = dataLines.join('\n')

  try {
    const parsed = JSON.parse(eventData)

    if (eventType === 'token') {
      callbacks.onToken(typeof parsed === 'string' ? parsed : parsed.text || eventData)
    } else if (eventType === 'status') {
      callbacks.onStatus(parsed.message || 'Processing...')
    } else if (eventType === 'done') {
      callbacks.onDone(mapDone(parsed))
    } else if (eventType === 'error') {
      callbacks.onError(parsed.message || 'Unknown error')
    }
  } catch {
    // Token events are plain text; status/done/error events are JSON.
    if (eventType === 'token' || !eventType) {
      callbacks.onToken(eventData)
    }
  }
}

export function streamChat(
  query: string,
  conversationId: string,
  threshold: number,
  callbacks: ChatCallbacks,
): AbortController {
  const controller = new AbortController()

  fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      query,
      conversation_id: conversationId,
      tool_threshold: threshold,
    }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) {
        callbacks.onError(`Server error: ${response.status}`)
        return
      }

      const reader = response.body?.getReader()
      if (!reader) {
        callbacks.onError('No response stream available')
        return
      }

      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) {
          buffer += decoder.decode()
          if (buffer.trim()) {
            dispatchSseEvent(buffer, callbacks)
          }
          break
        }

        buffer += decoder.decode(value, { stream: true })

        // Backend uses sse-starlette EventSourceResponse with sep="\r\n".
        const events = buffer.split(SSE_EVENT_DELIMITER)
        buffer = events.pop() || ''

        for (const eventBlock of events) {
          dispatchSseEvent(eventBlock, callbacks)
        }
      }
    })
    .catch((err) => {
      if (err.name !== 'AbortError') {
        callbacks.onError(err.message)
      }
    })

  return controller
}
