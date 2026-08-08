"""Rebuild every retrieval collection.

    PYTHONPATH=. .venv/bin/python -m app.knowledge.ingest

Run this after seeding, and after any change to the doctors, hospitals or
diagnostic-test tables. The provider directory is generated from those rows,
so re-ingesting is how a newly added doctor becomes findable by the assistant.

Idempotent: existing collections are recreated rather than appended to, so a
doctor who was removed from the database also disappears from search. That is
why this is a rebuild rather than an incremental upsert — a stale directory
entry pointing at a doctor who no longer practises is worse than a missing one.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger("ingest")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild SuwaPath retrieval collections.")
    parser.add_argument(
        "--probe",
        action="store_true",
        help="After ingesting, run a few sample queries to prove retrieval works.",
    )
    args = parser.parse_args()

    from app.services.knowledge import knowledge_service

    started = time.perf_counter()
    counts = knowledge_service.reindex()
    elapsed = time.perf_counter() - started

    logger.info("Backend: %s", knowledge_service.backend)
    for name, count in counts.items():
        logger.info("  %-22s %4d documents", name, count)
    logger.info("Done in %.1fs", elapsed)

    if not counts.get("provider_directory"):
        logger.warning(
            "The provider directory is empty. Seed the database first: "
            "PYTHONPATH=. .venv/bin/python -m app.seed.seeder --reset"
        )

    if args.probe:
        _probe(knowledge_service)

    return 0


def _probe(service) -> None:
    from app.services.knowledge import CLINICAL, POLICY, PROVIDERS

    checks = [
        (PROVIDERS, "dermatologist in Colombo who speaks Tamil"),
        (PROVIDERS, "where can I get an MRI scan and how much does it cost"),
        (PROVIDERS, "hospital with an emergency department in Kandy"),
        (CLINICAL, "what does low haemoglobin mean"),
        (POLICY, "can my husband see my medical reports"),
        (POLICY, "is the private chat really not saved"),
    ]

    print(f"\n{'=' * 74}\nRETRIEVAL PROBE\n{'=' * 74}")
    for collection, query in checks:
        results = service.search(query, limit=2, collection=collection)
        print(f"\n[{collection}] {query!r}")
        if not results:
            print("   (no results)")
            continue
        for result in results:
            print(f"   {result.score:.3f}  {result.doc.title}")
            print(f"          {result.doc.text[:150]}…")


if __name__ == "__main__":
    sys.exit(main())
