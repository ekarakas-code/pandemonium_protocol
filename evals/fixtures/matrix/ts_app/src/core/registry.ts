import type { Channel, EventEnvelope } from "./types.js";
import { EventDispatcher } from "./dispatcher.js";

/**
 * Tracks which handlers (channels) are interested in which topic and drives the
 * dispatcher when events come in. Both entry points push events through the
 * dispatcher so the registry never delivers to channels itself.
 */
export class HandlerRegistry {
  private readonly handlers = new Map<string, Channel[]>();

  constructor(private readonly dispatcher: EventDispatcher) {}

  /**
   * Register a channel for a topic, subscribe it on the dispatcher, and replay
   * the registration as an event so late subscribers see current state.
   */
  register(topic: string, channel: Channel): void {
    const list = this.handlers.get(topic) ?? [];
    list.push(channel);
    this.handlers.set(topic, list);
    this.dispatcher.subscribe(channel);
    this.dispatcher.dispatchEvent({
      id: `register:${topic}:${channel.name}`,
      topic,
      severity: "info",
      payload: { action: "register", channel: channel.name },
      receivedAt: Date.now(),
    });
  }

  /**
   * Resolve and flush every queued envelope for a topic by handing each one to
   * the dispatcher for fan-out to the registered handlers.
   */
  resolveAll(topic: string, queued: EventEnvelope[]): number {
    const handlers = this.handlers.get(topic) ?? [];
    if (handlers.length === 0) {
      return 0;
    }
    for (const env of queued) {
      this.dispatcher.dispatchEvent(env);
    }
    return queued.length;
  }
}
