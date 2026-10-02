import { useCallback, useEffect, useRef, useState } from "react";
import { errorMessage, getConversation } from "@/lib/api";
import {
  applyChatEvent,
  newerConversation,
  parseChatEvent,
} from "@/lib/chatState";
import { isActive } from "@/lib/types";
import type { Conversation } from "@/lib/types";

interface Selection {
  id: string;
  controller: AbortController;
  request: number;
}

export function useChat(id: string | null, onSettled: () => void) {
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [error, setError] = useState({ id, message: "" });
  const selected = useRef<Selection | null>(null);
  const refresh = useCallback(
    async (targetId = id) => {
      const selection = selected.current;
      if (!targetId || selection?.id !== targetId) return;
      const request = ++selection.request;
      let value: Conversation;
      try {
        value = await getConversation(targetId, selection.controller.signal);
      } catch (error) {
        if (
          selected.current !== selection ||
          selection.controller.signal.aborted ||
          request !== selection.request
        )
          return;
        throw error;
      }
      if (
        selected.current === selection &&
        !selection.controller.signal.aborted &&
        request === selection.request
      ) {
        setConversation((previous) => newerConversation(previous, value));
        setError({ id: targetId, message: "" });
      }
    },
    [id],
  );

  useEffect(() => {
    if (!id) return;
    const selection = { id, controller: new AbortController(), request: 0 };
    selected.current = selection;
    void refresh().catch((error: unknown) => {
      if (!selection.controller.signal.aborted)
        setError({ id, message: errorMessage(error) });
    });
    return () => {
      selection.controller.abort();
      selected.current = null;
    };
  }, [id, refresh]);

  const visible = conversation?.id === id ? conversation : null;
  const activeTurn = visible?.turns.find(isActive)?.id;
  const awaitingQuality = visible?.turns.some(
    (turn) =>
      ["pending", "running"].includes(turn.quality?.status || "") ||
      ["pending", "sending"].includes(turn.quality?.export_status || ""),
  );
  useEffect(() => {
    if (!id || !awaitingQuality) return;
    let stopped = false;
    let timer: number;
    async function poll() {
      try {
        await refresh();
      } catch (error) {
        if (!stopped) setError({ id, message: errorMessage(error) });
      } finally {
        if (!stopped) timer = window.setTimeout(() => void poll(), 5000);
      }
    }
    timer = window.setTimeout(() => void poll(), 5000);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
  }, [id, awaitingQuality, refresh]);

  useEffect(() => {
    if (!activeTurn || !id) return;
    const source = new EventSource(`/api/turns/${activeTurn}/events`);
    let stopped = false;
    let cursor = 0;
    const reportError = (error: unknown) => {
      if (!stopped) setError({ id, message: errorMessage(error) });
    };
    source.addEventListener("update", (event) => {
      if (stopped) return;
      try {
        const data: unknown = event.data;
        if (typeof data !== "string")
          throw new Error("Invalid stream event data.");
        const record = parseChatEvent(JSON.parse(data));
        if (record.conversation_id !== id || record.turn_id !== activeTurn) {
          throw new Error("Received an update for a different turn.");
        }
        if (record.sequence <= cursor) return;
        cursor = record.sequence;
        if (["approval_requested", "approval_resolved"].includes(record.type)) {
          void refresh().catch(reportError);
        }
        setConversation((previous) =>
          previous ? applyChatEvent(previous, record) : previous,
        );
      } catch (error) {
        source.close();
        reportError(error);
      }
    });
    source.addEventListener("settled", () => {
      source.close();
      void refresh()
        .then(() => {
          if (!stopped) onSettled();
        })
        .catch(reportError);
    });
    source.onerror = () =>
      reportError(
        new Error(
          "Connection interrupted. Reconnecting to the same turn - no resubmission.",
        ),
      );
    source.onopen = () => {
      if (!stopped) setError({ id, message: "" });
    };
    return () => {
      stopped = true;
      source.close();
    };
  }, [activeTurn, id, refresh, onSettled]);

  return {
    conversation: visible,
    refresh,
    loading: Boolean(id && !visible && !(error.id === id && error.message)),
    error: error.id === id ? error.message : "",
  };
}
