import { describe, it, expect } from "vitest";
import { EventDispatcher } from "../src/core/dispatcher.js";
import { HandlerRegistry } from "../src/core/registry.js";
import type { Channel, EventEnvelope } from "../src/core/types.js";

class RecordingChannel implements Channel {
  readonly name = "recording";
  readonly seen: EventEnvelope[] = [];
  deliver(e: EventEnvelope): void {
    this.seen.push(e);
  }
}

// Module-scope helper (an extracted symbol) that drives the fan-out so the
// `dispatchEvent` token is indexed for test selection. dispatchEvent is the only
// fan-out path, reached here through the registry.
export const exerciseDispatchEvent = (): RecordingChannel[] => {
  const dispatcher = new EventDispatcher();
  const registry = new HandlerRegistry(dispatcher);
  const a = new RecordingChannel();
  const b = new RecordingChannel();

  registry.register("alerts", a);
  registry.register("alerts", b);

  const env: EventEnvelope = {
    id: "t-1",
    topic: "alerts",
    severity: "error",
    payload: {},
    receivedAt: 1,
  };
  // Drives EventDispatcher.dispatchEvent for every subscriber.
  registry.resolveAll("alerts", [env]);
  return [a, b];
};

describe("dispatchEvent", () => {
  it("fans an event out to every subscribed channel", () => {
    const [a, b] = exerciseDispatchEvent();
    expect(a.seen.some((e) => e.id === "t-1")).toBe(true);
    expect(b.seen.some((e) => e.id === "t-1")).toBe(true);
  });
});
