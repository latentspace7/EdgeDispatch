import { z } from "zod";
import { decisionSchema, ledgerEventSchema } from "./types";
import type { Conversation, LedgerEvent } from "./types";

const attemptSchema = z.object({
  executor: z.enum(["LOCAL", "ESCALATE"]),
  attempt_id: z.string(),
});
const deltaSchema = z.object({ text: z.string(), attempt_id: z.string() });
const stateSchema = z.object({ state: z.string() });

export function parseChatEvent(value: unknown): LedgerEvent {
  const event = ledgerEventSchema.parse(value);
  switch (event.type) {
    case "decision":
      decisionSchema.parse(event.payload);
      break;
    case "attempt_started":
      attemptSchema.parse(event.payload);
      break;
    case "answer_delta":
      deltaSchema.parse(event.payload);
      break;
    case "turn_state":
      stateSchema.parse(event.payload);
      break;
  }
  return event;
}

export function applyChatEvent(
  conversation: Conversation,
  event: LedgerEvent,
): Conversation {
  if (
    event.conversation_id !== conversation.id ||
    event.sequence <= conversation.sequence
  ) {
    return conversation;
  }
  const turns = conversation.turns.map((turn) => {
    if (turn.id !== event.turn_id) return turn;
    switch (event.type) {
      case "decision":
        return { ...turn, decision: decisionSchema.parse(event.payload) };
      case "attempt_started":
        return { ...turn, ...attemptSchema.parse(event.payload), answer: "" };
      case "answer_superseded":
        return { ...turn, answer: "", attempt_id: null };
      case "answer_delta": {
        const delta = deltaSchema.parse(event.payload);
        return delta.attempt_id === turn.attempt_id
          ? { ...turn, answer: turn.answer + delta.text }
          : turn;
      }
      case "turn_state":
        return { ...turn, ...stateSchema.parse(event.payload) };
      default:
        return turn;
    }
  });
  return { ...conversation, turns, sequence: event.sequence };
}

export function newerConversation(
  previous: Conversation | null,
  incoming: Conversation,
) {
  return previous?.id === incoming.id && previous.sequence > incoming.sequence
    ? previous
    : incoming;
}
