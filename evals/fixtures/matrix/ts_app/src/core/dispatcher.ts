import type { Channel, EventEnvelope } from "./types.js";
import { normalizePayload } from "../transform/normalize.js";
import { logError } from "../diagnostics/logger.js";

/**
 * Core fan-out engine. An incoming event is normalized once and then delivered
 * to every subscribed channel, so a single event reaches all of its handlers.
 */
export class EventDispatcher {
  private readonly subscribers: Channel[] = [];

  subscribe(channel: Channel): void {
    this.subscribers.push(channel);
  }

  /**
   * Fan a single incoming event out to all subscribed channels. Each subscriber
   * gets the normalized envelope; a failing handler is logged and skipped so one
   * bad channel cannot stop delivery to the others.
   */
  dispatchEvent(env: EventEnvelope): void {
    const normalized = normalizePayload(env);
    for (const channel of this.subscribers) {
      try {
        channel.deliver(normalized);
      } catch (err) {
        logError(normalized.id, normalized.severity, err);
      }
    }
  }
}
