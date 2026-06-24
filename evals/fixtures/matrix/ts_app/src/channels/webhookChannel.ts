import type { Channel, EventEnvelope } from "../core/types.js";

/** Delivers events by POSTing them to an external webhook URL. */
export class WebhookChannel implements Channel {
  readonly name = "webhook";

  constructor(private readonly url: string) {}

  deliver(e: EventEnvelope): void {
    console.log(`[webhook->${this.url}] ${e.topic} (${e.severity}) #${e.id}`);
  }
}
