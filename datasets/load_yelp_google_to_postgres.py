"""Load DAB yelp and googlelocal into Postgres for Tapstate, data unchanged.

yelp -> database `yelpsrc`: business and checkin (from the MongoDB dump; nested fields kept as
JSON text, values as received), review, tip (+ surrogate row_id) and users (from DuckDB).
googlelocal -> database `googlesrc`: business_description (from its own SQL dump; PRIMARY KEY
gmap_id) and review (from SQLite, + surrogate row_id).

Usage: python load_yelp_google_to_postgres.py <dab-root>
"""
import json
import subprocess
import sqlite3
import sys
from pathlib import Path

import bson
import duckdb

from load_small_to_postgres import db, table

DAB = Path(sys.argv[1])


def load_yelp():
    q = DAB / "query_yelp" / "query_dataset"
    cur = db("yelpsrc")
    dump = q / "yelp_business" / "yelp_db"
    biz = bson.decode_all((dump / "business.bson").read_bytes())
    table(cur, "business", [("business_id", "TEXT"), ("name", "TEXT"), ("review_count", "BIGINT"),
                            ("is_open", "BIGINT"), ("attributes", "TEXT"), ("hours", "TEXT"),
                            ("description", "TEXT")], "business_id",
          [(d["business_id"], d.get("name"), d.get("review_count"), d.get("is_open"),
            json.dumps(d.get("attributes")) if d.get("attributes") is not None else None,
            json.dumps(d.get("hours")) if d.get("hours") is not None else None,
            d.get("description")) for d in biz])
    chk = bson.decode_all((dump / "checkin.bson").read_bytes())
    table(cur, "checkin", [("business_id", "TEXT"), ("date", "TEXT")], "business_id",
          [(d["business_id"], d.get("date")) for d in chk])
    c = duckdb.connect(str(q / "yelp_user.db"), read_only=True)
    table(cur, "review", [("review_id", "TEXT"), ("user_id", "TEXT"), ("business_ref", "TEXT"),
                          ("rating", "BIGINT"), ("useful", "BIGINT"), ("funny", "BIGINT"), ("cool", "BIGINT"),
                          ("text", "TEXT"), ("date", "TEXT")], "review_id",
          [tuple(r) for r in c.execute("select review_id, user_id, business_ref, rating, useful, funny, cool, "
                                       "text, date from review").fetchall()])
    table(cur, "tip", [("row_id", "BIGINT"), ("user_id", "TEXT"), ("business_ref", "TEXT"), ("text", "TEXT"),
                       ("date", "TEXT"), ("compliment_count", "BIGINT")], "row_id",
          [(i,) + tuple(r) for i, r in enumerate(c.execute(
              "select user_id, business_ref, text, date, compliment_count from tip").fetchall(), 1)])
    table(cur, "users", [("user_id", "TEXT"), ("name", "TEXT"), ("review_count", "BIGINT"),
                         ("yelping_since", "TEXT"), ("useful", "BIGINT"), ("funny", "BIGINT"), ("cool", "BIGINT"),
                         ("elite", "TEXT")], "user_id",
          [tuple(r) for r in c.execute("select user_id, name, review_count, yelping_since, useful, funny, cool, "
                                       "elite from \"user\"").fetchall()])


def load_google():
    q = DAB / "query_googlelocal" / "query_dataset"
    cur = db("googlesrc")
    subprocess.run(["docker", "exec", "-i", "ts-postgres-1", "psql", "-q", "-U", "postgres", "-d", "googlesrc"],
                   input=(q / "business_description.sql").read_bytes(), check=True, capture_output=True)
    cur.execute('ALTER TABLE public.business_description RENAME COLUMN "MISC" TO misc')
    cur.execute("ALTER TABLE public.business_description ADD PRIMARY KEY (gmap_id)")
    cur.execute("ALTER TABLE public.business_description REPLICA IDENTITY FULL")
    s = sqlite3.connect(q / "review_query.db")
    cols = [r[1] for r in s.execute("pragma table_info(review)")]
    rows = [(i,) + r for i, r in enumerate(s.execute(f"select {', '.join(cols)} from review"), 1)]
    types = {"rating": "DOUBLE PRECISION"}
    table(cur, "review", [("row_id", "BIGINT")] + [(c, types.get(c, "TEXT")) for c in cols], "row_id", rows)


if __name__ == "__main__":
    load_yelp()
    load_google()
