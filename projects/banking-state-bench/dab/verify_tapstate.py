"""Check Tapstate's consolidated view against upstream, and optionally make it setup C.

Reads MongoDB views.bank_customer (written by the bank_customer_state pipeline), puts every
document in canonical array order, and compares it with the upstream record exactly as
build_consolidated.py checks its own build. With --wait, first waits until the view holds every
customer and has stopped changing. With --install, and only if every document matches, makes
Tapstate's output setup C: bank_state.customer, its dump, and query_bank_consolidated's config.

Usage: python verify_tapstate.py <dab-root> [--wait] [--install]
"""
import sys
import time
from pathlib import Path

from pymongo import MongoClient

from build_consolidated import install, verify
from common import MAP_ID, MAPS, MONGO_URI, STATE_DB, consolidated_doc, load_customers


def as_record(doc):
    """A consolidated document back to the upstream record layout, so it can be canonicalized."""
    rec = {k: doc[k] for k in ("profile", "login_context", "mandates", "offers")}
    for k in MAPS:
        rec[k] = {x[MAP_ID[k]]: x for x in doc[k]}
    return rec


def wait_for_view(view, want, timeout=1800, settle=30):
    """Block until the view holds `want` documents and has stopped changing for `settle` seconds."""
    start, last, since = time.time(), None, time.time()
    while time.time() - start < timeout:
        state = (view.count_documents({}), view.database.command("collstats", view.name).get("size"))
        if state != last:
            last, since = state, time.time()
            print(f"view: {state[0]} of {want} documents", flush=True)
        elif state[0] >= want and time.time() - since >= settle:
            return
        time.sleep(5)
    sys.exit(f"the view did not settle at {want} documents within {timeout}s (last: {last[0]})")


def main():
    dab = Path(sys.argv[1]).resolve()
    m = MongoClient(MONGO_URI)
    if "--wait" in sys.argv:
        wait_for_view(m["views"]["bank_customer"], len(load_customers()))
    docs = list(m["views"]["bank_customer"].find({}, {"_id": 0}))
    if not docs:
        sys.exit("views.bank_customer is empty: apply and start the bank_customer_state pipeline first")
    built = {d["customer_id"]: consolidated_doc(d["customer_id"], as_record(d)) for d in docs}
    problems = verify(built, load_customers())
    if problems:
        sys.exit(f"{len(problems)} differences from upstream, e.g.:\n" + "\n".join(problems[:10]))
    print(f"verified: Tapstate's {len(built)} customer documents equal the upstream records field for field")
    if "--install" in sys.argv:
        install(dab, built)
        print(f"installed Tapstate's output as setup C ({STATE_DB}.customer)")


if __name__ == "__main__":
    main()
