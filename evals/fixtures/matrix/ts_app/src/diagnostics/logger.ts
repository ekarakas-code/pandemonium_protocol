import type { Severity } from "../core/types.js";

/**
 * Emit a structured error line. The distinctive trace prefix below is what
 * surfaces in logs when a delivery handler blows up on a specific envelope.
 */
export const logError = (envelopeId: string, severity: Severity, err: unknown): void => {
  const reason = err instanceof Error ? err.message : String(err);
  console.error(`dispatch: handler threw for envelope id=${envelopeId} severity=${severity}: ${reason}`);
};
