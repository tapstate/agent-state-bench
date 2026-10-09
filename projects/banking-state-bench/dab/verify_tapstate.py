"""Check Tapstate's consolidated view against upstream, and optionally make it setup C.

Reads MongoDB views.bank_customer (written by the bank_customer_state pipeline), puts every
document in canonical array order, and compares it with the upstream record exactly as
build_consolidated.py checks its own build. With --install, and only if every document matches,
replaces bank_state.customer and its dump with Tapstate's output.

Usage: python verify_tapstate.py <dab-root> [--install]
"""
import sys
from pathlib import Path

from pymongo import MongoClient

from build_consolidated import verify
from common import CONSOLIDATED, MAP_ID, MAPS, MONGO_URI, STATE_DB, consolidated_doc, load_customers
from split_sources import write_mongo_dump


def as_record(doc):
    """A consolidated document back to the upstream record layout, so it can be canonicalized."""
    rec = {k: doc[k] for k in ("profile", "login_context", "mandates", "offers")}
    for k in MAPS:
        rec[k] = {x[MAP_ID[k]]: x for x in doc[k]}
    return rec


def main():
    dab = Path(sys.argv[1]).resolve()
    m = MongoClient(MONGO_URI)
    docs = list(m["views"]["bank_customer"].find({}, {"_id": 0}))
    if not docs:
        sys.exit("views.bank_customer is empty: apply and start the bank_customer_state pipeline first")
    built = {d["customer_id"]: consolidated_doc(d["customer_id"], as_record(d)) for d in docs}
    problems = verify(built, load_customers())
    if problems:
        sys.exit(f"{len(problems)} differences from upstream, e.g.:\n" + "\n".join(problems[:10]))
    print(f"verified: Tapstate's {len(built)} customer documents equal the upstream records field for field")
    if "--install" in sys.argv:
        m.drop_database(STATE_DB)
        rows = [{"_id": cid, **d} for cid, d in built.items()]
        m[STATE_DB]["customer"].insert_many(rows)
        m[STATE_DB]["customer"].create_index("profile.segment")
        write_mongo_dump(dab / f"query_{CONSOLIDATED}" / "query_dataset" / f"{STATE_DB}_dump", STATE_DB,
                         {"customer": rows})
        print(f"installed Tapstate's output as {STATE_DB}.customer")


if __name__ == "__main__":
    main()
