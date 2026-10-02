import { z } from "zod";

export const policySchema = z.enum([
  "reconsider_each_turn",
  "sticky_escalation",
]);
export const preferencesSchema = z.object({
  policy: policySchema,
  pricing_model: z.string(),
  pricing_version: z.string(),
  input_rate: z.number().nonnegative().nullable(),
  cached_input_rate: z.number().nonnegative().nullable(),
  output_rate: z.number().nonnegative().nullable(),
});
export const decisionSchema = z.object({
  route: z.enum(["LOCAL", "ESCALATE"]),
  reason: z.string(),
  raw: z.string(),
});
export const qualitySchema = z.object({
  status: z.string(),
  export_status: z.string().optional(),
});
export const turnSchema = z.object({
  id: z.string(),
  conversation_id: z.string(),
  request_id: z.string(),
  query: z.string(),
  answer: z.string(),
  state: z.string(),
  attempt_id: z.string().nullable().optional(),
  executor: z.enum(["LOCAL", "ESCALATE"]).optional(),
  decision: decisionSchema.nullish(),
  error: z.string().optional(),
  reason: z.string().optional(),
  latency_ms: z.number().nonnegative().optional(),
  approvals: z
    .array(
      z.object({
        action_id: z.string(),
        tool: z.string(),
        arguments: z.unknown(),
      }),
    )
    .default([]),
  quality: qualitySchema.optional(),
});
export const metricsSchema = z.object({
  completed_local: z.number(),
  remote_used_turns: z.number(),
  completed: z.number(),
  pending: z.number(),
  failed_or_cancelled: z.number(),
  decision_calls: z.number(),
  local_execution_calls: z.number(),
  remote_call_attempts: z.number(),
  tool_calls: z.number(),
  known_api_cost_usd: z.string(),
  api_cost_complete: z.boolean(),
  unknown_cost_attempts: z.number(),
  estimated_always_remote_cost_usd: z.number().nullable(),
  estimated_cost_avoided_usd: z.number().nullable(),
});
export const conversationSummarySchema = z.object({
  id: z.string(),
  title: z.string(),
  policy: policySchema,
  sticky: z.boolean(),
  updated_at: z.string(),
  sequence: z.number().int().nonnegative(),
  metrics: metricsSchema,
});
export const conversationSchema = conversationSummarySchema.extend({
  turns: z.array(turnSchema),
});
export const healthSchema = z.object({
  status: z.string(),
  redis: z.boolean(),
  local_model: z.boolean(),
  artifacts_verified: z.boolean(),
  remote_configured: z.boolean(),
  remote_access_tested: z.boolean(),
  remote_access_error: z.string(),
  mcp_servers: z.array(z.string()),
  unavailable_mcp_servers: z.array(z.string()),
  local_model_name: z.string(),
  remote_model_name: z.string(),
  context_tokens: z.number().int().nonnegative(),
  output_tokens: z.number().int().positive(),
});
export const ledgerEventSchema = z.object({
  sequence: z.number().int().positive(),
  conversation_id: z.string(),
  turn_id: z.string(),
  type: z.string(),
  timestamp: z.string(),
  payload: z.record(z.string(), z.unknown()),
});
export const pendingTurnSchema = z.object({
  conversation_id: z.uuid(),
  request_id: z.uuid(),
  query: z.string().min(1).max(100_000),
  force_remote: z.boolean(),
});

export type Policy = z.infer<typeof policySchema>;
export type Preferences = z.infer<typeof preferencesSchema>;
export type Turn = z.infer<typeof turnSchema>;
export type ConversationSummary = z.infer<typeof conversationSummarySchema>;
export type Conversation = z.infer<typeof conversationSchema>;
export type Health = z.infer<typeof healthSchema>;
export type LedgerEvent = z.infer<typeof ledgerEventSchema>;

export const isActive = (turn: Turn) =>
  ![
    "completed",
    "failed",
    "cancelled",
    "interrupted",
    "needs_reconciliation",
  ].includes(turn.state);
