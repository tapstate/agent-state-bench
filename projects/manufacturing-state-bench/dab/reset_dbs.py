"""Restore every server-side manufacturing database from the dataset files, before a batch of runs.

Runs never change the databases, but a batch should still start from exactly the published data,
whatever ran before:

  Postgres mfg_erp, mfg_costing   from query_manufacturing/query_dataset/erp.sql, costing.sql
  MongoDB  mfg_wms                from query_manufacturing/query_dataset/mfg_wms_dump
  MongoDB  mfg_state              from query_manufacturing_consolidated/query_dataset/mfg_state_dump (setup C)

The SQLite and DuckDB sources are files in query_manufacturing/query_dataset. Tapstate reads its
own copy (Postgres mfgsrc), so restoring these databases never disturbs a running pipeline.

Usage: python reset_dbs.py <dab-root>
"""
import sys
from pathlib import Path

import bson
from pymongo import MongoClient

from common import CONSOLIDATED, DATASET, MONGO_URI, STATE_DB, WMS_DB
from split_sources import load_server_side


def restore_mongo(m, db, dump):
    files = sorted(dump.glob("*.bson"))
    if not files:
        sys.exit(f"no dump in {dump}")
    m.drop_database(db)
    counts = {}
    for f in files:
        docs = bson.decode_all(f.read_bytes())
        m[db][f.stem].insert_many(docs)
        counts[f.stem] = len(docs)
    return counts


def main():
    dab = Path(sys.argv[1]).resolve()
    load_server_side(dab / f"query_{DATASET}")   # mfg_erp, mfg_costing and mfg_wms, as split_sources.py loads them
    m = MongoClient(MONGO_URI)
    state_dump = dab / f"query_{CONSOLIDATED}" / "query_dataset" / f"{STATE_DB}_dump" / STATE_DB
    counts = restore_mongo(m, STATE_DB, state_dump)
    print(f"restored MongoDB {STATE_DB}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    # check: every restored collection holds exactly its dump's documents
    for db, dump in ((WMS_DB, dab / f"query_{DATASET}" / "query_dataset" / f"{WMS_DB}_dump" / WMS_DB),
                     (STATE_DB, state_dump)):
        for f in sorted(dump.glob("*.bson")):
            want = sorted(map(bson.encode, bson.decode_all(f.read_bytes())))
            got = sorted(map(bson.encode, m[db][f.stem].find({})))
            if want != got:
                sys.exit(f"{db}.{f.stem} differs from its dump after restore")
    print("checked: MongoDB collections equal their dumps")


if __name__ == "__main__":
    main()
