# llmforge

Wiring LLMs into real codebases: a bounded agent loop, context packing,
guardrails, a variance-aware eval harness, a tamper-evident audit trail, and
four coding principles compiled into gates that can fail.

Zero required runtime dependencies. The Anthropic SDK is optional and lazily
imported, so the library, its tests, and its examples run offline against
`FakeProvider`.

```bash
pip install -e ".[anthropic]"     # omit the extra to stay offline
```

```python
from llmforge import Agent, Budget, FakeProvider

agent = Agent(FakeProvider(["done"]), budget=Budget(max_steps=10, max_usd=1.00))
print(agent.run("go").text)
```

Full documentation, the assurance doctrine, and the frontier labs live in the
repository root: <https://github.com/warnetech/claude-code-assist>
