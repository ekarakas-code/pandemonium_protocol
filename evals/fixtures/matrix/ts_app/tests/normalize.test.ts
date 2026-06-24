import { describe, it, expect } from "vitest";
import { normalizePayload } from "../src/transform/normalize.js";
import type { EventEnvelope } from "../src/core/types.js";

describe("normalizePayload", () => {
  it("lowercases the topic and defaults missing fields", () => {
    const raw = {
      id: "n-1",
      topic: "  ALERTS  ",
      payload: { a: 1 },
    } as unknown as EventEnvelope;

    const out = normalizePayload(raw);

    expect(out.topic).toBe("alerts");
    expect(out.severity).toBe("info");
    expect(out.receivedAt).toBeGreaterThan(0);
  });

  it("preserves an explicit severity and timestamp", () => {
    const env: EventEnvelope = {
      id: "n-2",
      topic: "Audit",
      severity: "critical",
      payload: {},
      receivedAt: 42,
    };

    const out = normalizePayload(env);

    expect(out.topic).toBe("audit");
    expect(out.severity).toBe("critical");
    expect(out.receivedAt).toBe(42);
  });
});
