import { describe, it, expect, vi } from "vitest";
import type { Channel, EventEnvelope } from "../src/core/types.js";
import { EmailChannel } from "../src/channels/emailChannel.js";
import { SmsChannel } from "../src/channels/smsChannel.js";
import { WebhookChannel } from "../src/channels/webhookChannel.js";

const env: EventEnvelope = {
  id: "c-1",
  topic: "alerts",
  severity: "warning",
  payload: {},
  receivedAt: 1,
};

describe("Channel implementations", () => {
  const channels: Channel[] = [
    new EmailChannel("ops@example.com"),
    new SmsChannel("+10000000000"),
    new WebhookChannel("https://hooks.example.com/x"),
  ];

  it("every Channel impl delivers without throwing", () => {
    const spy = vi.spyOn(console, "log").mockImplementation(() => {});
    for (const channel of channels) {
      expect(() => channel.deliver(env)).not.toThrow();
    }
    spy.mockRestore();
  });

  it("EmailChannel exposes its name", () => {
    expect(new EmailChannel("x@y.z").name).toBe("email");
  });
});
