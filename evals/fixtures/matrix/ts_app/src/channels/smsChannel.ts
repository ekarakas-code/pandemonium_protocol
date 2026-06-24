import type { Channel, EventEnvelope } from "../core/types.js";

/** Delivers events as SMS text messages. */
export class SmsChannel implements Channel {
  readonly name = "sms";

  constructor(private readonly phone: string) {}

  deliver(e: EventEnvelope): void {
    console.log(`[sms->${this.phone}] ${e.topic} (${e.severity}) #${e.id}`);
  }
}
