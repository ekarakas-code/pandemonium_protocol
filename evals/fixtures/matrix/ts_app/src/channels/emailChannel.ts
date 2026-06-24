import type { Channel, EventEnvelope } from "../core/types.js";

/** Delivers events as email notifications. */
export class EmailChannel implements Channel {
  readonly name = "email";

  constructor(private readonly to: string) {}

  deliver(e: EventEnvelope): void {
    console.log(`[email->${this.to}] ${e.topic} (${e.severity}) #${e.id}`);
  }
}
