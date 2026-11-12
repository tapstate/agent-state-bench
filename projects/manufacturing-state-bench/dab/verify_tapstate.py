"""Check Tapstate's four consolidated views against the reference, and optionally make them setup C.

Reads MongoDB views.mfg_product, mfg_work_order, mfg_sales_order and mfg_purchase_order (written by
the pipelines in tapstate/), puts every array in canonical order, and compares every document,
field for field, with common.reference_state(), which is built from the raw AdventureWorks tables
by their integer ids, as build_consolidated.py checks its own build. Tapstate writes the sources' SQL
DATEs as BSON dates (midnight UTC); they are compared as the reference's 'YYYY-MM-DD' (common.iso_dates),
and installed as BSON dates. With --wait, first waits
until each view holds every document and has stopped changing. With --install, and only if every
document matches, makes Tapstate's output setup C: MongoDB mfg_state, its dump, and
query_manufacturing_consolidated's config.

Usage: python verify_tapstate.py <dab-root> [--wait] [--install]
"""
import sys
import time
from pathlib import Path

from pymongo import MongoClient

from build_consolidated import install
from common import COLLECTIONS, ID, MONGO_URI, STATE_DB, canonical, iso_dates, reference_state, verify

VIEWS = {"products": "mfg_product", "work_orders": "mfg_work_order", "sales_orders": "mfg_sales_order",
         "purchase_orders": "mfg_purchase_order"}


def wait_for_views(db, want, timeout=3600, settle=60):
    """Block until every view holds its document count and none has changed for `settle` seconds."""
    start, last, since = time.time(), None, time.time()
    while time.time() - start < timeout:
        state = tuple((db[v].count_documents({}), db.command("collstats", v).get("size") if v in
                       db.list_collection_names() else 0) for v in VIEWS.values())
        if state != last:
            last, since = state, time.time()
            print("views: " + ", ".join(f"{v} {n}/{want[c]}" for (c, v), (n, _) in zip(VIEWS.items(), state)),
                  flush=True)
        elif all(n >= want[c] for c, (n, _) in zip(VIEWS, state)) and time.time() - since >= settle:
            return
        time.sleep(10)
    sys.exit(f"the views did not settle within {timeout}s")


def main():
    dab = Path(sys.argv[1]).resolve()
    reference = reference_state()
    views = MongoClient(MONGO_URI)["views"]
    if "--wait" in sys.argv:
        wait_for_views(views, {c: len(reference[c]) for c in COLLECTIONS})
    built = {}
    for c, v in VIEWS.items():
        docs = list(views[v].find({}, {"_id": 0}))
        if not docs:
            sys.exit(f"views.{v} is empty: run run_tapstate.sh first")
        built[c] = {d[ID[c]]: canonical(c, d) for d in docs}
    problems = verify({c: {k: iso_dates(d) for k, d in docs.items()} for c, docs in built.items()}, reference)
    if problems:
        sys.exit(f"{len(problems)} differences from the reference, e.g.:\n" + "\n".join(problems[:10]))
    print("verified: Tapstate's " + ", ".join(f"{len(built[c])} {c}" for c in COLLECTIONS)
          + " equal the reference built from the raw tables, field for field")
    if "--install" in sys.argv:
        install(dab, built)
        print(f"installed Tapstate's output as setup C ({STATE_DB})")


if __name__ == "__main__":
    main()
