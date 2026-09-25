"""Build Arm C's dataset directories for DAB bookreview and music_brainz_20k.

Copies the Tapstate views into one MongoDB database per dataset, dumps it, and writes
query_<dataset>_consolidated/ (db_config.yaml, db_description.txt, a copy of every query
directory). Descriptions state the layer's guarantees and fields only: no DAB hint text, no
per-query guidance.

Usage: python make_small_datasets.py <dab-root>
"""
import shutil
import subprocess
import sys
from pathlib import Path

from pymongo import MongoClient

DAB = Path(sys.argv[1]) if len(sys.argv) > 1 else None

DATASETS = {
    "bookreview": ("book_state", ["book"], """You are working with one database, book_state, stored in MongoDB.

book_state holds the consolidated state of a book store's catalog and its customer reviews,
maintained continuously from the store's product and review databases.

Guarantees:
- One document per book, identified by `book_id`. Each book embeds all of its reviews.
- Facts that the sources keep inside free text or JSON strings are extracted into fields:
  author name, categories (array), publication date / year / decade, language, publisher,
  format, page count. A field is null when the source does not state it.

Collections in book_state:
- book: one document per book
  Fields: book_id, title, subtitle, author_name, rating_number (number of ratings on the store),
  price, store, categories (array, broad to narrow), publication_date (YYYY-MM-DD or YYYY-MM),
  publication_year, publication_decade (e.g. 1990), language, publisher, format, pages,
  features, description, details (as received)
  - reviews: array of this book's reviews
    Fields: row_id, rating (1-5), title, text, review_time, review_date (YYYY-MM-DD), helpful_vote,
    verified_purchase (boolean), book_id
"""),
    "music_brainz_20k": ("music_state", ["song", "track", "sale"], """You are working with one database, music_state, stored in MongoDB.

music_state holds the consolidated state of a music catalog and its sales. The catalog is
assembled from five source catalogs that list the same songs under different track ids and with
inconsistent titles, artists and albums; they are resolved into songs here.

Guarantees:
- A song is one real-world recording, identified by `song_id`. Every source track belongs to
  exactly one song; `song_id` is the same in every collection.
- Song title, artist and album are clean canonical values (source formatting and typos removed).
- Every sale carries the song it belongs to, so a song's totals are the sum over its sales.

Collections in music_state:
- song: one document per song
  Fields: song_id, title, artist, album, year, n_records (number of source tracks resolved into it)
- track: one document per source track
  Fields: track_id, song_id, source_id, source_track_id, title, artist, album (all as received),
  release_year, track_length, track_language (as received)
- sale: one document per sale record
  Fields: sale_id, track_id, song_id, song_title, song_artist, song_album, country, store,
  units_sold, revenue_usd
"""),
}


def main():
    build(DAB, DATASETS)


def build(dab, datasets):
    """Copy each dataset's Tapstate views into its own database, dump it, and write the
    dataset directory setup C runs against."""
    m = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
    for ds, (db, views, description) in datasets.items():
        m.drop_database(db)
        for v in views:
            if m["views"][v].count_documents({}) == 0:
                raise SystemExit(f"view {v} is empty")
            m["views"][v].aggregate([{"$project": {"_id": 0}}, {"$out": {"db": db, "coll": v}}])
        if db == "music_state":
            for v in ("track", "sale"):
                m[db][v].create_index("song_id")
                assert m[db][v].count_documents({"song_id": None}) == 0, f"{v} has unresolved rows"
        src, dst = dab / f"query_{ds}", dab / f"query_{ds}_consolidated"
        dst.mkdir(exist_ok=True)
        dump = dst / "query_dataset" / f"{db}_dump"
        shutil.rmtree(dump, ignore_errors=True)
        dump.mkdir(parents=True)
        subprocess.run(["docker", "run", "--rm", "--network", "ts_default", "-v", f"{dump}:{dump}", "mongo:7.0",
                        "mongodump", "--quiet", "--uri", "mongodb://mongo:27017/?directConnection=true",
                        f"--db={db}", f"--out={dump}"], check=True)
        (dst / "db_config.yaml").write_text(
            f"db_clients:\n  {db}:\n    db_type: mongo\n    db_name: {db}\n    dump_folder: {dump.relative_to(dst)}\n")
        (dst / "db_description.txt").write_text(description)
        for q in sorted(src.glob("query[0-9]*")):
            d = dst / q.name
            d.mkdir(exist_ok=True)
            for f in ("query.json", "validate.py", "ground_truth.csv"):
                if (q / f).exists():
                    shutil.copy(q / f, d / f)
        print(f"wrote {dst}")


if __name__ == "__main__":
    main()
