import type { EventEnvelope } from "../core/types.js";
import { normalizePayload } from "./normalize.js";

const SECRET_KEYS = ["password", "token", "secret", "apiKey"];

/**
 * Strip secret-looking keys from the event payload. Like every stage that runs
 * before delivery, it normalizes the payload first (via normalizePayload) so
 * redaction always operates on the canonical, normalized envelope, then masks
 * any sensitive fields.
 */
export const redactSecrets = (e: EventEnvelope): EventEnvelope => {
  // Normalize the payload before delivery, then redact secrets from it.
  const normalized = normalizePayload(e);
  const payload: Record<string, unknown> = { ...normalized.payload };
  for (const key of Object.keys(payload)) {
    if (SECRET_KEYS.includes(key)) {
      payload[key] = "***";
    }
  }
  return { ...normalized, payload };
};
