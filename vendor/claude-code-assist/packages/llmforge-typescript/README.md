# @llmforge/core

Wiring LLMs into real codebases: a bounded agent loop, guardrails, a
tamper-evident audit trail, and four coding principles compiled into gates that
can fail.

Zero runtime dependencies. `@anthropic-ai/sdk` is an optional peer, lazily
imported, so the library and its tests run offline against `FakeProvider`.

```bash
npm install @llmforge/core @anthropic-ai/sdk
```

```typescript
import { Agent, FakeProvider } from "@llmforge/core";

const agent = new Agent(new FakeProvider(["done"]), {
  budget: { maxSteps: 10, maxUsd: 1.0 },
});
console.log((await agent.run("go")).text);
```

Full documentation, the assurance doctrine, and the frontier labs live in the
repository root: <https://github.com/warnetech/claude-code-assist>
