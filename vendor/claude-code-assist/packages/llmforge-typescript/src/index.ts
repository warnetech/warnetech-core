/**
 * llmforge -- wiring LLMs into real codebases.
 *
 *   provider   the vendor seam (real, fake, retrying)
 *   trace      spans and a cost ledger
 *   tools      typed tools, schemas, gated dispatch
 *   loop       a bounded agent loop that handles the awkward stop reasons
 *   guard      untrusted-content envelopes, secret redaction, output validation
 *   assurance  four coding principles compiled into gates that can fail
 *   audit      tamper-evident trail, deny-by-default authority, dual control
 *   labs       frontier techniques, each with a falsifiable claim
 *
 * Nothing here requires the Anthropic SDK to import. Everything can be
 * exercised against `FakeProvider` with no network.
 */

export const VERSION = "0.1.0";

export * from "./types.js";
export * from "./provider.js";
export * from "./tools.js";
export * from "./trace.js";
export * from "./loop.js";
export * from "./guard.js";
export * from "./assurance.js";
export * from "./audit.js";
