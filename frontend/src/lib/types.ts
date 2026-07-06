export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: number
  // Per-query metadata populated from the SSE `done` event (assistant only).
  cost?: CostBreakdown
  wasEscalated?: boolean
  toolCount?: number
  handoff?: HandoffDetails
}

export interface Conversation {
  id: string
  title: string
  messages: Message[]
  createdAt: number
  updatedAt: number
}

export interface Settings {
  toolThreshold: number
  priceInputPerMTok: number
  priceOutputPerMTok: number
  localModel: string
  highEndModel: string
  mcpServerCount: number
  arizeEndpoint: string
}

export interface StreamStatus {
  type: 'analyzing' | 'routing'
  message: string
  estimatedTools?: number
  routeDecision?: string
  threshold?: number
  conversationId?: string
  messageId?: string
}

export interface CostBreakdown {
  route: string
  price_input_per_mtok: number
  price_output_per_mtok: number
  tokens_mono_in: number
  tokens_mono_out: number
  tokens_ed_in: number
  tokens_ed_out: number
  observed_cloud_in: number
  observed_cloud_out: number
  observed_local_in: number
  observed_local_out: number
  c_mono: number
  c_ed: number
  delta_c: number
  schema_tokens_avoided: number
}

export interface HandoffDetails {
  query: string
  selectedTools: string[]
  rationale: string
  evidence: unknown[]
  toolThreshold: number
  prompt: string
}

export interface StreamDone {
  conversationId: string
  wasEscalated: boolean
  toolCount: number
  threshold: number
  cost?: CostBreakdown
  evaluation?: Record<string, unknown>
  handoff?: HandoffDetails
}
