"""End-to-end tour of warnetech-core. Runs offline — no API keys required.

    python examples/quickstart.py
"""

from __future__ import annotations

import asyncio

from warnetech_core import Message, Settings


async def main() -> None:
    # Offline settings so this runs anywhere. In a real project this is just
    # `gateway, memory = build(project="hub-notes")` reading the environment.
    settings = Settings.from_env(
        offline=True, memory_backend="memory", usage_backend="memory", project="hub-notes"
    )
    gateway = settings.build_gateway()
    memory = settings.build_memory(gateway)

    # 1. Prompts are versioned data, not string literals.
    gateway.prompts.add(
        "qa.grounded",
        [
            ("system", "Answer only from the context. Say so if it is not there."),
            ("user", "<context>\n{{ context }}\n</context>\n\nQuestion: {{ question }}"),
        ],
        version=1,
        model="chat.default",
    )

    # 2. Memory is namespaced per project.
    await memory.remember_many(
        "hub-notes",
        [
            {"text": "We chose Postgres for the billing database.",
             "key": "adr-1", "metadata": {"kind": "decision"}},
            {"text": "Standup moved to 09:30 on Tuesdays.", "key": "standup"},
            {"text": "The staging cluster runs in eu-west-1.", "key": "infra"},
        ],
    )
    # A different project cannot see any of that.
    await memory.remember("hub-invoices", "Acme owes EUR 4,200, due March.", key="acme")

    # 3. Retrieve, then answer.
    question = "which database did we pick for billing?"
    # min_score keeps unrelated records out of the prompt instead of padding it.
    context = await memory.get_context(
        "hub-notes", question, include_scores=True, min_score=0.1
    )
    print(f"retrieved {len(context.hits)} memories from {context.namespaces}")
    for hit in context.hits:
        print(f"  {hit.score:.3f}  {hit.record.key}: {hit.record.text[:50]}")

    response = await gateway.complete_prompt(
        "qa.grounded",
        {"context": context.text, "question": question},
        project="hub-notes",
    )
    print(f"\nanswer: {response.text[:120]}...")

    # 4. Isolation holds unless you ask for it.
    leaked = await memory.recall("hub-notes", "acme invoice")
    crossed = await memory.recall("hub-notes", "acme invoice", also_projects=["hub-invoices"])
    print(f"\nprojects reachable by default:  {sorted({h.record.project for h in leaked})}")
    print(f"projects with also_projects:    {sorted({h.record.project for h in crossed})}")

    # 5. Swapping a model is one alias change; downstream code is untouched.
    gateway.registry.alias("chat.default", "echo.chat")
    await gateway.complete("chat.default", [Message.user("still works")], project="hub-notes")

    # 6. Everything above was accounted for.
    print("\nusage by operation:")
    for row in gateway.usage.summary(group_by="operation"):
        print(f"  {row.key:10} calls={row.calls} tokens={row.total_tokens} "
              f"cost=${row.cost_usd:.6f}")

    await memory.aclose()
    await gateway.aclose()


if __name__ == "__main__":
    asyncio.run(main())
