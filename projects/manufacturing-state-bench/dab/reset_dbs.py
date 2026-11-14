"""Before a batch of runs: restore setup C, and check (or, with --sources, reload) the six source databases.

Runs never change the databases (SQL runs read-only; Mongo queries only read), but a batch should still
start from exactly the published data, whatever ran before:

  MongoDB mfg_state   restored from query_manufacturing_consolidated/query_dataset/mfg_state_dump (setup C)
  the six sources     Postgres mfg_erp, mfg_plm, mfg_costing; MySQL mfg_mes, mfg_procurement; MongoDB
                      mfg_wms: each table's row count is checked against split_sources.systems(), and
                      mfg_wms against its dump, document for document

Tapstate reads the six source databases themselves, so reloading them (--sources) drops what its
pipelines capture from: stop the pipelines first and rerun dab/run_tapstate.sh after.

Usage: python reset_dbs.py <dab-root> [--sources]
"""
import sys
from pathlib import Path

import bson
from pymongo import MongoClient

import psycopg2
import pymysql

from common import CONSOLIDATED, DATASET, MONGO_SOURCES_URI, MONGO_URI, MYSQL, PG, STATE_DB, WMS_DB
from split_sources import MYSQL_SYSTEMS, PG_SYSTEMS, load_server_side, systems


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


def check_source_counts():
    """Every SQL source table holds as many rows as split_sources.systems() writes."""
    sy = systems()
    for engines, connect in ((PG_SYSTEMS, lambda db: psycopg2.connect(dbname=db, **PG)),
                             (MYSQL_SYSTEMS, lambda db: pymysql.connect(database=db, **MYSQL))):
        for system, (db, _) in engines.items():
            cur = connect(db).cursor()
            for table, (_, rows, _) in sy[system].items():
                cur.execute(f"SELECT count(*) FROM {table}")
                n = cur.fetchone()[0]
                if n != len(rows):
                    sys.exit(f"{db}.{table} holds {n} rows, the split writes {len(rows)}")
    print("checked: the SQL sources hold every row the split writes")


def main():
    dab = Path(sys.argv[1]).resolve()
    if "--sources" in sys.argv:
        load_server_side(dab / f"query_{DATASET}")   # the six databases, as split_sources.py loads them
    check_source_counts()
    m = MongoClient(MONGO_URI)
    state_dump = dab / f"query_{CONSOLIDATED}" / "query_dataset" / f"{STATE_DB}_dump" / STATE_DB
    counts = restore_mongo(m, STATE_DB, state_dump)
    print(f"restored MongoDB {STATE_DB}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))

    # check: every restored collection holds exactly its dump's documents
    for client, db, dump in ((MongoClient(MONGO_SOURCES_URI), WMS_DB,
                              dab / f"query_{DATASET}" / "query_dataset" / f"{WMS_DB}_dump" / WMS_DB),
                             (m, STATE_DB, state_dump)):
        for f in sorted(dump.glob("*.bson")):
            want = sorted(map(bson.encode, bson.decode_all(f.read_bytes())))
            got = sorted(map(bson.encode, client[db][f.stem].find({})))
            if want != got:
                sys.exit(f"{db}.{f.stem} differs from its dump after restore")
    print("checked: MongoDB collections equal their dumps")


if __name__ == "__main__":
    main()
