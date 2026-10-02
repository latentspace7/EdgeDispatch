import { z } from "zod";
import {
  conversationSchema,
  conversationSummarySchema,
  healthSchema,
  pendingTurnSchema,
  preferencesSchema,
  turnSchema,
} from "./types";

export const errorMessage = (error: unknown) =>
  error instanceof z.ZodError
    ? "Received invalid data. Refresh status and check the connection."
    : error instanceof Error
      ? error.message
      : "An unexpected error occurred.";

export async function api(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
): Promise<unknown> {
  const response = await fetch(`/api${path}`, {
    method,
    signal,
    headers: method === "GET" ? {} : { "Content-Type": "application/json" },
    body: method === "GET" ? undefined : JSON.stringify(body ?? {}),
  });
  if (!response.ok) {
    const error: unknown = await response.json().catch(() => null);
    const parsed = z.object({ detail: z.string() }).safeParse(error);
    throw new Error(
      parsed.success
        ? parsed.data.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}

export const listConversations = async (signal?: AbortSignal) =>
  z
    .array(conversationSummarySchema)
    .parse(await api("/conversations", "GET", undefined, signal));
export const createConversation = async () =>
  conversationSchema.parse(await api("/conversations", "POST"));
export const getConversation = async (id: string, signal?: AbortSignal) => {
  const value = conversationSchema.parse(
    await api(`/conversations/${id}`, "GET", undefined, signal),
  );
  if (value.id !== id)
    throw new Error("The response belongs to a different conversation.");
  return value;
};
export const getHealth = async (signal?: AbortSignal) =>
  healthSchema.parse(await api("/health", "GET", undefined, signal));
export const getSettings = async (signal?: AbortSignal) =>
  preferencesSchema.parse(await api("/settings", "GET", undefined, signal));

function pendingTurn(conversationId: string) {
  const saved = sessionStorage.getItem(
    `edgedispatch-pending:${conversationId}`,
  );
  if (!saved) return null;
  const pending = pendingTurnSchema.parse(JSON.parse(saved));
  if (pending.conversation_id !== conversationId) {
    throw new Error(
      "The unconfirmed message belongs to a different conversation.",
    );
  }
  return pending;
}

export async function sendTurn(
  conversationId: string,
  query: string,
  forceRemote: boolean,
) {
  const key = `edgedispatch-pending:${conversationId}`;
  const pending =
    pendingTurn(conversationId) ??
    pendingTurnSchema.parse({
      conversation_id: conversationId,
      request_id: crypto.randomUUID(),
      query,
      force_remote: forceRemote,
    });
  if (pending.query !== query || pending.force_remote !== forceRemote) {
    throw new Error(
      "A previous message has unconfirmed delivery. Use Retry delivery before sending another message.",
    );
  }
  sessionStorage.setItem(key, JSON.stringify(pending));
  const turn = turnSchema.parse(await api("/chat", "POST", pending));
  if (
    turn.conversation_id !== conversationId ||
    turn.request_id !== pending.request_id
  ) {
    throw new Error(
      "The delivery response does not match the pending message.",
    );
  }
  sessionStorage.removeItem(key);
  return turn;
}

export async function retryDelivery(conversationId: string) {
  const pending = pendingTurn(conversationId);
  if (!pending) throw new Error("No unconfirmed delivery in this tab.");
  return sendTurn(conversationId, pending.query, pending.force_remote);
}
