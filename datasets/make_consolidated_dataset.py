"""Build Arm C's dataset directory: the crmarenapro queries pointed at the consolidated state.

1. Copies the Tapstate views (MongoDB `views` database) into a database named `crm_state` and
   dumps it, so the harness restores it the same way it restores any MongoDB dataset.
2. Writes query_crmarenapro_consolidated/ in the DAB checkout: db_config.yaml (one MongoDB
   database), db_description.txt (generated from the pipeline definitions and the source
   columns), and a copy of every query directory (query.json, validate.py, ground_truth.csv),
   so each arm logs into its own tree while the questions and validators stay identical.
3. Registers the dataset name in run_agent.py.

The description states only what the layer guarantees (one document per entity, normalized
identifiers, embedded children). It carries no DAB hint text and no per-query guidance.

Usage: python make_consolidated_dataset.py <dab-root>
"""
import shutil
import subprocess
import sys
from pathlib import Path

import psycopg2
from pymongo import MongoClient

from gen_crm_pipelines import VIEWS

DAB = Path(sys.argv[1])
SRC = DAB / "query_crmarenapro"
DST = DAB / "query_crmarenapro_consolidated"
DB = "crm_state"

mongo = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
pg = psycopg2.connect("host=127.0.0.1 port=55432 user=postgres password=secret dbname=crm").cursor()


def columns(table):
    pg.execute("select column_name from information_schema.columns where table_schema='public' "
               "and table_name=%s order by ordinal_position", (table,))
    return [r[0] for r in pg.fetchall()]


def copy_views():
    mongo.drop_database(DB)
    for view in VIEWS:
        n = mongo["views"][view].count_documents({})
        if n == 0:
            raise SystemExit(f"view {view} is empty; wait for its pipeline before building the dataset")
        mongo["views"][view].aggregate([{"$project": {"_id": 0}}, {"$out": {"db": DB, "coll": view}}])
        mongo[DB][view].create_index("id", unique=True)
    dump = DST / "query_dataset" / "crm_state_dump"
    shutil.rmtree(dump, ignore_errors=True)
    dump.mkdir(parents=True)
    subprocess.run(["docker", "run", "--rm", "--network", "ts_default", "-v", f"{dump}:{dump}",
                    "mongo:7.0", "mongodump", "--quiet", "--uri",
                    "mongodb://mongo:27017/?directConnection=true", f"--db={DB}", f"--out={dump}"],
                   check=True)
    return dump


def describe_embeds(embeds, depth):
    lines = []
    for table, fk, path, nested in embeds:
        pad = "  " * depth
        lines.append(f"{pad}- {path}: array of {table} records whose {fk} equals this document's id")
        lines.append(f"{pad}  Fields: {', '.join(columns(table))}")
        lines += describe_embeds(nested, depth + 1)
    return lines


def description():
    out = [
        f"You are working with one database, {DB}, stored in MongoDB.",
        "",
        f"{DB} holds the consolidated current state of a company's CRM: sales pipeline, customer",
        "support, products, orders, users and knowledge articles. It is maintained continuously from",
        "the company's operational databases.",
        "",
        "Guarantees:",
        "- Each collection holds one document per entity, identified by the field `id`.",
        "- Identifiers are normalized: every reference field (a field named `...id` or `...id__c`)",
        "  holds exactly the `id` of the document it refers to. Text values are trimmed.",
        "- Child records are embedded in their parent document as arrays, as listed below. The same",
        "  child record may also exist as its own collection.",
        "- Field names are lower-case. Dates are ISO-8601 strings.",
        "- Embedded arrays can be long (an opportunity carries all of its tasks, call transcripts and",
        "  emails), so whole documents can be large; use a projection to fetch only the fields you need.",
        "",
        f"Collections in {DB}:",
    ]
    for view, (root, embeds) in VIEWS.items():
        out.append(f"- {view}: one document per {root} record")
        out.append(f"  Fields: {', '.join(columns(root))}")
        out += describe_embeds(embeds, 1)
    return "\n".join(out) + "\n"


def main():
    DST.mkdir(exist_ok=True)
    dump = copy_views()
    (DST / "db_config.yaml").write_text(
        f"db_clients:\n  {DB}:\n    db_type: mongo\n    db_name: {DB}\n"
        f"    dump_folder: {dump.relative_to(DST)}\n")
    (DST / "db_description.txt").write_text(description())
    for q in sorted(SRC.glob("query[0-9]*")):
        d = DST / q.name
        d.mkdir(exist_ok=True)
        for f in ("query.json", "validate.py", "ground_truth.csv"):
            if (q / f).exists():
                shutil.copy(q / f, d / f)
    run_agent = DAB / "run_agent.py"
    s = run_agent.read_text()
    if '"crmarenapro_consolidated"' not in s:
        s = s.replace('    "crmarenapro",\n', '    "crmarenapro",\n    "crmarenapro_consolidated",\n', 1)
        run_agent.write_text(s)
    print(f"wrote {DST} ({len(list(DST.glob('query[0-9]*')))} queries), dump at {dump}")


if __name__ == "__main__":
    main()
