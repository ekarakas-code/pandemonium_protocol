// Shared types for the event pipeline: the envelope that flows through the
// dispatcher and the Channel extension point that every delivery target implements.

export type Severity = "info" | "warning" | "error" | "critical";

export type EventEnvelope = {
  id: string;
  topic: string;
  severity: Severity;
  payload: Record<string, unknown>;
  receivedAt: number;
};

/**
 * A delivery channel. Implement this interface to add a new place an event can
 * be sent (email, sms, webhook, ...). `deliver` is the single extension hook.
 */
export interface Channel {
  readonly name: string;
  deliver(e: EventEnvelope): void;
}
