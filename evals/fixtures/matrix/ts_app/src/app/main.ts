import type { EventEnvelope } from "../core/types.js";
import { EventDispatcher } from "../core/dispatcher.js";
import { HandlerRegistry } from "../core/registry.js";
import { EmailChannel } from "../channels/emailChannel.js";
import { SmsChannel } from "../channels/smsChannel.js";
import { WebhookChannel } from "../channels/webhookChannel.js";

/** Wire up the pipeline and push a couple of events through the registry. */
export function main(): void {
  const dispatcher = new EventDispatcher();
  const registry = new HandlerRegistry(dispatcher);

  registry.register("alerts", new EmailChannel("ops@example.com"));
  registry.register("alerts", new SmsChannel("+10000000000"));
  registry.register("audit", new WebhookChannel("https://hooks.example.com/audit"));

  const queued: EventEnvelope[] = [
    {
      id: "evt-1",
      topic: "alerts",
      severity: "critical",
      payload: { message: "disk almost full" },
      receivedAt: Date.now(),
    },
  ];

  registry.resolveAll("alerts", queued);
}

main();
