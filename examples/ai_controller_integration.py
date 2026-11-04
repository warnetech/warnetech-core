"""Plugging warnetech-core into warnetech_ai_controller.

Runs offline — no API keys, and no dependency on claude-command-cli being
importable: the two AI-controller functions it touches are reproduced here
verbatim in behaviour so the seam can be demonstrated standalone.

    python examples/ai_controller_integration.py

The real call sites are:

    from warnetech_ai_controller.embeddings import generate_slice_embedding
    from warnetech_ai_controller.relevance import rank_by_similarity
"""

from __future__ import annotations

import json
import math

from warnetech_core import AIGateway
from warnetech_core.integrations import make_embed_fn
from warnetech_core.sync import SyncRunner


# --- stand-ins for warnetech_ai_controller, unchanged in behaviour ---------

def generate_slice_embedding(slice_metadata: dict, embed_fn) -> list[float]:
    """`warnetech_ai_controller.embeddings.generate_slice_embedding`."""
    return embed_fn(json.dumps(slice_metadata, sort_keys=True))


def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def rank_by_similarity(query_embedding, entries, min_similarity=0.15):
    """`warnetech_ai_controller.relevance.rank_by_similarity`."""
    scored = [
        {**e, "similarity": cosine_similarity(query_embedding, e.get("embedding", []))}
        for e in entries
    ]
    scored = [e for e in scored if e["similarity"] >= min_similarity]
    scored.sort(key=lambda e: e["similarity"], reverse=True)
    return scored


# --- the integration ------------------------------------------------------

def main() -> None:
    gateway = AIGateway.offline()

    with SyncRunner() as runner:
        # This is the whole seam: a synchronous Callable[[str], list[float]]
        # backed by the gateway, with retry, fallback and cost attribution.
        embed = make_embed_fn(gateway, project="warnetech-firewall", runner=runner)

        slices = [
            {"id": "s1", "system": "edge", "type": "attack",
             "summary": "SQL injection attempt against the login endpoint"},
            {"id": "s2", "system": "edge", "type": "attack",
             "summary": "brute force password spray on /admin"},
            {"id": "s3", "system": "node-4", "type": "metric",
             "summary": "disk utilisation nominal, no action required"},
        ]

        indexed = [
            {**s, "embedding": generate_slice_embedding(s, embed_fn=embed)} for s in slices
        ]

        query = "sql injection on login"
        ranked = rank_by_similarity(embed(query), indexed)

        print(f"query: {query!r}\n")
        for entry in ranked:
            print(f"  {entry['similarity']:.3f}  {entry['id']}  {entry['summary']}")
        if not ranked:
            print("  (nothing above the relevance threshold)")

        dropped = {s["id"] for s in slices} - {e["id"] for e in ranked}
        print(f"\nfiltered out below threshold: {sorted(dropped) or 'none'}")

    print("\nembedding spend, attributed to the AI controller's own project:")
    for row in gateway.usage.summary(group_by="project"):
        print(f"  {row.key:22} calls={row.calls} tokens={row.total_tokens} "
              f"cost=${row.cost_usd:.6f}")


if __name__ == "__main__":
    main()
