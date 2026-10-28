"""Load DAB agnews into Postgres for Tapstate, data unchanged: database `agnewssrc` with articles
(from the MongoDB dump), authors and article_metadata (from SQLite).

Usage: python load_agnews_to_postgres.py <dab-root>
"""
import sqlite3
import sys
from pathlib import Path

import bson

from load_small_to_postgres import db, table

DAB = Path(sys.argv[1])

if __name__ == "__main__":
    q = DAB / "query_agnews" / "query_dataset"
    cur = db("agnewssrc")
    docs = bson.decode_all((q / "agnews_articles" / "articles_db" / "articles.bson").read_bytes())
    table(cur, "articles", [("article_id", "BIGINT"), ("title", "TEXT"), ("description", "TEXT")], "article_id",
          [(d["article_id"], d.get("title"), d.get("description")) for d in docs])
    s = sqlite3.connect(q / "metadata.db")
    table(cur, "authors", [("author_id", "BIGINT"), ("name", "TEXT")], "author_id",
          list(s.execute("select author_id, name from authors")))
    table(cur, "article_metadata", [("article_id", "BIGINT"), ("author_id", "BIGINT"), ("region", "TEXT"),
                                    ("publication_date", "TEXT")], "article_id",
          list(s.execute("select article_id, author_id, region, publication_date from article_metadata")))
