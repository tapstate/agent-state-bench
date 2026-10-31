"""Restore every server-side bank database from the dataset files, before a batch of runs.

Runs never change the databases, but the freshness extension does: it replays transactions into
Postgres bank_cbs and converts its dates to text. Run this before every source-split batch so the
agents see exactly the published data, whatever ran before:

  Postgres bank_cbs         from query_bank/query_dataset/cbs.sql
  MongoDB  bank_crm         from query_bank/query_dataset/bank_crm_dump
  MongoDB  bank_state       from query_bank_consolidated/query_dataset/bank_state_dump (setup C)

The SQLite and DuckDB sources are files in query_bank/query_dataset and are opened read-only.
Refuses while a replication slot still reads bank_cbs (a running freshness pipeline): stop that
pipeline and drop its slot first.

Usage: python reset_dbs.py <dab-root>
"""
import sys
from pathlib import Path

import bson
import psycopg2
from pymongo import MongoClient

from common import CBS_DB, CONSOLIDATED, CRM_DB, DATASET, MONGO_URI, PG, STATE_DB
from split_sources import load_server_side


def restore_mongo(m, db, dump):
    files = sorted(dump.glob("*.bson"))
    if not files:
        sys.exit(f"no dump in {dump}")
    m.drop_database(db)
    for f in files:
        m[db][f.stem].insert_many(bson.decode_all(f.read_bytes()))
    return {f.stem: len(bson.decode_all(f.read_bytes())) for f in files}


def main():
    dab = Path(sys.argv[1]).resolve()
    admin = psycopg2.connect(dbname="postgres", **PG)
    cur = admin.cursor()
    cur.execute("SELECT slot_name FROM pg_replication_slots WHERE database = %s", (CBS_DB,))
    slots = [r[0] for r in cur.fetchall()]
    if slots:
        sys.exit(f"{CBS_DB} is read by replication slot(s) {', '.join(slots)}: stop the freshness pipeline "
                 f"and drop the slot(s) first (SELECT pg_drop_replication_slot('<name>'))")
    admin.close()

    load_server_side(dab / f"query_{DATASET}")  # bank_cbs and bank_crm, as split_sources.py loads them
    m = MongoClient(MONGO_URI)
    counts = restore_mongo(m, STATE_DB, dab / f"query_{CONSOLIDATED}" / "query_dataset" / f"{STATE_DB}_dump" / STATE_DB)
    m[STATE_DB]["customer"].create_index("profile.segment")
    print(f"restored MongoDB {STATE_DB}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    # check: every restored collection holds exactly its dump's documents
    for db, dump in ((CRM_DB, dab / f"query_{DATASET}" / "query_dataset" / f"{CRM_DB}_dump" / CRM_DB),
                     (STATE_DB, dab / f"query_{CONSOLIDATED}" / "query_dataset" / f"{STATE_DB}_dump" / STATE_DB)):
        for f in sorted(dump.glob("*.bson")):
            want = sorted(map(bson.encode, bson.decode_all(f.read_bytes())))
            got = sorted(map(bson.encode, m[db][f.stem].find({})))
            if want != got:
                sys.exit(f"{db}.{f.stem} differs from its dump after restore")
    print("checked: MongoDB collections equal their dumps")


if __name__ == "__main__":
    main()
