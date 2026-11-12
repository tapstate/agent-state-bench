"""Load the six manufacturing source systems into Postgres for Tapstate, values and key formats unchanged.

Reads the files split_sources.py wrote (setup A's query_dataset), not the raw AdventureWorks
tables, so Tapstate consolidates exactly what the agents in setup A query. One database, `mfgsrc`,
with one schema per system: erp, plm, mes, wms, procurement, costing. Table names are unique
across schemas, since pipeline steps name tables without a schema. Every table keeps its primary
key and gets REPLICA IDENTITY FULL for change capture.

Dates are stored as ISO text (YYYY-MM-DD), the same values: on Tapstate a SQL DATE reaches a `js`
step as an opaque object (see the bank benchmark's SOURCE_SPLIT.md §3), and the pipelines only
pass dates through.

Usage: python load_mfg_to_postgres.py <dab-root>
"""
import sqlite3
import sys
from pathlib import Path

import bson
import duckdb
import psycopg2
from psycopg2.extras import execute_values

from common import DATASET, PG, SRC_DB, WMS_DB

FILES = {"plm": "plm.db", "mes": "mes.duckdb", "procurement": "procurement.duckdb"}
PG_FILES = {"erp": "erp.sql", "costing": "costing.sql"}
# WMS is a MongoDB dump: collection -> (DDL, primary key)
WMS = {"stock": ("_id BIGINT, item TEXT, location_id BIGINT, shelf TEXT, bin BIGINT, quantity BIGINT", "_id"),
       "locations": ("_id BIGINT, name TEXT, cost_rate NUMERIC(19,6), availability NUMERIC(19,6)", "_id")}


def pg_type(t):
    t = t.upper()
    if t in ("DATE",):
        return "TEXT"
    if t.startswith(("DECIMAL", "NUMERIC")):
        return "NUMERIC(19,6)"
    if t in ("REAL", "DOUBLE"):
        return "DOUBLE PRECISION"
    if t in ("INTEGER", "BIGINT"):
        return "BIGINT"
    return "TEXT"


def describe_sqlite(con, name):
    info = con.execute(f"PRAGMA table_info({name})").fetchall()
    cols = [(r[1], r[2]) for r in info]
    pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]]
    return cols, pk


def describe_duckdb(con, name):
    cols = [(r[0], r[1]) for r in con.execute(f"DESCRIBE {name}").fetchall()]
    pk = con.execute("SELECT constraint_column_names FROM duckdb_constraints() WHERE table_name = ? "
                     "AND constraint_type = 'PRIMARY KEY'", [name]).fetchone()[0]
    return cols, list(pk)


def create(cur, schema, name, cols, pk, rows):
    cur.execute(f"CREATE TABLE {schema}.{name} ({', '.join(f'{c} {t}' for c, t in cols)}, "
                f"PRIMARY KEY ({', '.join(pk)}))")
    execute_values(cur, f"INSERT INTO {schema}.{name} VALUES %s",
                   [tuple(v.isoformat() if hasattr(v, "isoformat") else v for v in r) for r in rows], page_size=5000)
    cur.execute(f"ALTER TABLE {schema}.{name} REPLICA IDENTITY FULL")
    print(f"{schema + '.' + name:34s} {len(rows):7d}")


def main():
    qd = Path(sys.argv[1]).resolve() / f"query_{DATASET}" / "query_dataset"
    admin = psycopg2.connect(dbname="postgres", **PG)
    admin.autocommit = True
    admin.cursor().execute(f"DROP DATABASE IF EXISTS {SRC_DB} WITH (FORCE)")
    admin.cursor().execute(f"CREATE DATABASE {SRC_DB}")
    con = psycopg2.connect(dbname=SRC_DB, **PG)
    con.autocommit = True
    cur = con.cursor()
    for schema in ("erp", "plm", "mes", "wms", "procurement", "costing"):
        cur.execute(f"CREATE SCHEMA {schema}")

    # the Postgres systems: their own SQL files, as written for setup A; dates converted to text
    for schema, f in PG_FILES.items():
        cur.execute(f"SET search_path TO {schema}")
        cur.execute((qd / f).read_text())
        cur.execute("SELECT table_name, column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema = %s ORDER BY table_name, ordinal_position", (schema,))
        cols = cur.fetchall()
        for t, c, typ in cols:
            if typ == "date":
                cur.execute(f"ALTER TABLE {schema}.{t} ALTER COLUMN {c} TYPE TEXT")
        for t in sorted({t for t, _, _ in cols}):
            cur.execute(f"ALTER TABLE {schema}.{t} REPLICA IDENTITY FULL")
            cur.execute(f"SELECT count(*) FROM {schema}.{t}")
            print(f"{schema + '.' + t:34s} {cur.fetchone()[0]:7d}")
    cur.execute("SET search_path TO public")

    for schema, f in FILES.items():
        if f.endswith(".db"):
            s = sqlite3.connect(qd / f)
            for (name,) in s.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY rowid"):
                cols, pk = describe_sqlite(s, name)
                create(cur, schema, name, [(c, pg_type(t)) for c, t in cols], pk,
                       s.execute(f"SELECT * FROM {name}").fetchall())
        else:
            d = duckdb.connect(str(qd / f), read_only=True)
            for (name,) in d.execute("SELECT table_name FROM duckdb_tables() ORDER BY table_name").fetchall():
                cols, pk = describe_duckdb(d, name)
                create(cur, schema, name, [(c, pg_type(t)) for c, t in cols], pk,
                       d.execute(f"SELECT * FROM {name}").fetchall())

    dump = qd / f"{WMS_DB}_dump" / WMS_DB
    for name, (ddl, pk) in WMS.items():
        cols = [c.split()[0] for c in ddl.split(", ")]
        docs = bson.decode_all((dump / f"{name}.bson").read_bytes())
        create(cur, "wms", name, [(c.split()[0], " ".join(c.split()[1:])) for c in ddl.split(", ")], [pk],
               [tuple(doc.get(c) for c in cols) for doc in docs])


if __name__ == "__main__":
    main()
