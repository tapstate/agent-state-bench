"""Load DAB bookreview and music_brainz_20k into Postgres for Tapstate, data unchanged.

bookreview -> database `booksrc`: books_info (from its own SQL dump; PRIMARY KEY book_id) and
review (from SQLite, plus a surrogate row_id primary key).
music_brainz_20k -> database `musicsrc`: tracks (PRIMARY KEY track_id) and sales (PRIMARY KEY
sale_id), from SQLite and DuckDB.

Usage: python load_small_to_postgres.py <dab-root>
"""
import sqlite3
import subprocess
import sys
from pathlib import Path

import duckdb
import psycopg2
from psycopg2.extras import execute_values

DAB = Path(sys.argv[1])
DSN = "host=127.0.0.1 port=55432 user=postgres password=secret"


def db(name):
    admin = psycopg2.connect(f"{DSN} dbname=postgres")
    admin.autocommit = True
    admin.cursor().execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.cursor().execute(f"CREATE DATABASE {name}")
    c = psycopg2.connect(f"{DSN} dbname={name}")
    c.autocommit = True
    return c.cursor()


def table(cur, name, cols, pk, rows):
    cur.execute(f"CREATE TABLE {name} ({', '.join(f'{c} {t}' for c, t in cols)}, PRIMARY KEY ({pk}))")
    execute_values(cur, f"INSERT INTO {name} VALUES %s", rows, page_size=5000)
    cur.execute(f"ALTER TABLE {name} REPLICA IDENTITY FULL")
    print(f"{name:12s} {len(rows):7d} rows")


def main():
    q = DAB / "query_bookreview" / "query_dataset"
    cur = db("booksrc")
    dump = (q / "books_info.sql").read_bytes()
    subprocess.run(["docker", "exec", "-i", "ts-postgres-1", "psql", "-q", "-U", "postgres", "-d", "booksrc"],
                   input=dump, check=True, capture_output=True)
    cur.execute("ALTER TABLE public.books_info ADD PRIMARY KEY (book_id)")
    cur.execute("ALTER TABLE public.books_info REPLICA IDENTITY FULL")
    cur.execute("select count(*) from books_info")
    print(f"{'books_info':12s} {cur.fetchone()[0]:7d} rows")
    s = sqlite3.connect(q / "review_query.db")
    rows = [(i,) + r for i, r in enumerate(s.execute(
        "select rating, title, text, review_time, helpful_vote, verified_purchase, purchase_id from review"), 1)]
    table(cur, "review", [("row_id", "BIGINT"), ("rating", "DOUBLE PRECISION"), ("title", "TEXT"), ("text", "TEXT"),
                          ("review_time", "TEXT"), ("helpful_vote", "BIGINT"), ("verified_purchase", "TEXT"),
                          ("purchase_id", "TEXT")], "row_id", rows)

    q = DAB / "query_music_brainz_20k" / "query_dataset"
    cur = db("musicsrc")
    s = sqlite3.connect(q / "tracks.db")
    rows = list(s.execute("select track_id, source_id, source_track_id, title, artist, album, year, length, "
                          "language from tracks"))
    table(cur, "tracks", [("track_id", "BIGINT"), ("source_id", "BIGINT"), ("source_track_id", "TEXT"),
                          ("title", "TEXT"), ("artist", "TEXT"), ("album", "TEXT"), ("year", "TEXT"),
                          ("length", "TEXT"), ("language", "TEXT")], "track_id", rows)
    d = duckdb.connect(str(q / "sales.duckdb"), read_only=True)
    rows = [tuple(r) for r in d.execute(
        "select sale_id, track_id, country, store, units_sold, revenue_usd from sales").fetchall()]
    table(cur, "sales", [("sale_id", "BIGINT"), ("track_id", "BIGINT"), ("country", "TEXT"), ("store", "TEXT"),
                         ("units_sold", "BIGINT"), ("revenue_usd", "DOUBLE PRECISION")], "sale_id", rows)


if __name__ == "__main__":
    main()
