"""Build Arm C2 for DAB bookreview: Arm C's book_state without the question-shaped field.

`publication_decade` exists in Arm C only because bookreview query 1 asks for a decade. C2 keeps
every other field, so the agent derives the decade from `publication_year` itself. The field is
computed on its own in the pipeline's js step (one line, from publication_year), so dropping it
from the Tapstate-built view equals running the pipeline without that line.

Writes MongoDB database book_state_v2 and query_bookreview_consolidated_v2/.

Usage: python make_book_v2_dataset.py <dab-root>
"""
import shutil
import subprocess
import sys
from pathlib import Path

from pymongo import MongoClient

from make_small_datasets import DATASETS

DAB = Path(sys.argv[1])
DB = "book_state_v2"


def main():
    _, _, description = DATASETS["bookreview"]
    for old, new in (("publication date / year / decade", "publication date / year"),
                     ("\n  publication_year, publication_decade (e.g. 1990), language,", "\n  publication_year, language,")):
        assert description.count(old) == 1, old
        description = description.replace(old, new)
    description = description.replace("book_state", DB)
    m = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
    m.drop_database(DB)
    m["views"]["book"].aggregate([{"$project": {"_id": 0, "publication_decade": 0}}, {"$out": {"db": DB, "coll": "book"}}])
    assert m[DB]["book"].count_documents({}) == m["views"]["book"].count_documents({}) == 200
    assert m[DB]["book"].count_documents({"publication_decade": {"$exists": True}}) == 0
    src, dst = DAB / "query_bookreview", DAB / "query_bookreview_consolidated_v2"
    dst.mkdir(exist_ok=True)
    dump = dst / "query_dataset" / f"{DB}_dump"
    shutil.rmtree(dump, ignore_errors=True)
    dump.mkdir(parents=True)
    subprocess.run(["docker", "run", "--rm", "--network", "ts_default", "-v", f"{dump}:{dump}", "mongo:7.0",
                    "mongodump", "--quiet", "--uri", "mongodb://mongo:27017/?directConnection=true",
                    f"--db={DB}", f"--out={dump}"], check=True)
    (dst / "db_config.yaml").write_text(
        f"db_clients:\n  {DB}:\n    db_type: mongo\n    db_name: {DB}\n    dump_folder: {dump.relative_to(dst)}\n")
    (dst / "db_description.txt").write_text(description)
    for q in sorted(src.glob("query[0-9]*")):
        d = dst / q.name
        d.mkdir(exist_ok=True)
        for f in ("query.json", "validate.py", "ground_truth.csv"):
            if (q / f).exists():
                shutil.copy(q / f, d / f)
    print(f"wrote {dst}")
    print(description)


if __name__ == "__main__":
    main()
