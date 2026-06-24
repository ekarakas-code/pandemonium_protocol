import type { EventEnvelope } from "../core/types.js";

/**
 * Normalize an incoming event payload before it is delivered: lowercase the
 * topic, default the severity, and stamp a receivedAt if one is missing. This
 * is the canonical shape every channel expects, so it is called everywhere a
 * payload enters the pipeline.
 */
export const normalizePayload = (e: EventEnvelope): EventEnvelope => {
  return {
    ...e,
    topic: (e.topic ?? "").trim().toLowerCase(),
    severity: e.severity ?? "info",
    payload: e.payload ?? {},
    receivedAt: e.receivedAt || Date.now(),
  };
};
