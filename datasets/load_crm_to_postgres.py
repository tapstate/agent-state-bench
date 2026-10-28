"""Load DAB crmarenapro (6 databases: SQLite, DuckDB, a Postgres dump) into one Postgres
database, unchanged except for naming, so a Tapstate pipeline can read it.

The data is copied as-is: dirty IDs (leading '#', trailing spaces) are kept, because
cleaning them is the consolidation layer's job, not the loader's.

Naming: columns are lower-cased (Postgres folds unquoted identifiers anyway, and the
DAB Postgres dump already arrives lower-cased); tables whose names are SQL keywords are
renamed (Case -> support_case, Order -> sales_order, User -> crm_user). Every table gets PRIMARY KEY (id),
which the raw ids satisfy.

Usage: python load_crm_to_postgres.py <dab-root> [postgres-dsn]
"""
import sqlite3
import sys
from pathlib import Path

import duckdb
import psycopg2
from psycopg2.extras import execute_values

ROOT = Path(sys.argv[1]) / "query_crmarenapro" / "query_dataset"
DSN = sys.argv[2] if len(sys.argv) > 2 else "host=127.0.0.1 port=55432 user=postgres password=secret"
RENAME = {"case": "support_case", "order": "sales_order", "user": "crm_user"}


def pg(db):
    c = psycopg2.connect(f"{DSN} dbname={db}")
    c.autocommit = True
    return c


def target_name(t):
    t = t.lower()
    return RENAME.get(t, t)


def write(dst, table, cols, types, rows):
    name = target_name(table)
    cur = dst.cursor()
    cur.execute(f'DROP TABLE IF EXISTS "{name}"')
    coldefs = ", ".join(f'"{c.lower()}" {types[i]}' for i, c in enumerate(cols))
    cur.execute(f'CREATE TABLE "{name}" ({coldefs}, PRIMARY KEY ("id"))')
    if rows:
        execute_values(cur, f'INSERT INTO "{name}" VALUES %s', rows, page_size=1000)
    cur.execute(f'ALTER TABLE "{name}" REPLICA IDENTITY FULL')
    print(f"{name:32s} {len(rows):6d} rows")


def sqlite_type(t):
    t = (t or "").upper()
    if "INT" in t:
        return "BIGINT"
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "DOUBLE PRECISION"
    if "BOOL" in t:
        return "BOOLEAN"
    return "TEXT"


def duck_type(t):
    t = t.upper()
    if t in ("BIGINT", "INTEGER", "SMALLINT", "TINYINT", "HUGEINT"):
        return "BIGINT"
    if t in ("DOUBLE", "FLOAT", "REAL") or t.startswith("DECIMAL"):
        return "DOUBLE PRECISION"
    if t == "BOOLEAN":
        return "BOOLEAN"
    return "TEXT"


def main():
    admin = pg("postgres")
    cur = admin.cursor()
    for db in ("crm", "crm_stage"):
        cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {db}")
    dst = pg("crm")

    for f in ("core_crm.db", "products_orders.db", "territory.db"):
        src = sqlite3.connect(ROOT / f)
        for (t,) in src.execute("select name from sqlite_master where type='table'"):
            info = list(src.execute(f'pragma table_info("{t}")'))
            cols = [r[1] for r in info]
            types = [sqlite_type(r[2]) for r in info]
            rows = [tuple(None if v is None else (str(v) if types[i] == "TEXT" else v)
                          for i, v in enumerate(r))
                    for r in src.execute(f'select * from "{t}"')]
            write(dst, t, cols, types, rows)

    for f in ("sales_pipeline.duckdb", "activities.duckdb"):
        src = duckdb.connect(str(ROOT / f), read_only=True)
        for (t,) in src.execute("show tables").fetchall():
            info = src.execute(f'describe "{t}"').fetchall()
            cols = [r[0] for r in info]
            types = [duck_type(r[1]) for r in info]
            rows = [tuple(None if v is None else (str(v) if types[i] == "TEXT" else v)
                          for i, v in enumerate(r))
                    for r in src.execute(f'select * from "{t}"').fetchall()]
            write(dst, t, cols, types, rows)

    # The support database ships as a SQL dump; run it in a staging database, then copy.
    stage = pg("crm_stage")
    stage.cursor().execute((ROOT / "support.sql").read_text())
    scur = stage.cursor()
    scur.execute("select table_name from information_schema.tables where table_schema='public'")
    for (t,) in scur.fetchall():
        scur.execute("select column_name, data_type from information_schema.columns "
                     "where table_schema='public' and table_name=%s order by ordinal_position", (t,))
        info = scur.fetchall()
        cols = [c for c, _ in info]
        types = ["TEXT" if d == "text" else d.upper() for _, d in info]
        scur.execute(f'select * from "{t}"')
        write(dst, t, cols, types, scur.fetchall())
    stage.close()
    cur.execute("DROP DATABASE crm_stage WITH (FORCE)")


if __name__ == "__main__":
    main()
