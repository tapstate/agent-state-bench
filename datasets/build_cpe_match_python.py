"""Build the cve `cpe_match` view outside Tapstate, with the pipeline's exact logic.

Why: the Tapstate pipeline for this view (585k-row fact LEFT JOIN a 550k-row crosswalk LEFT JOIN
a 585k-row version table, then js) cannot run on the benchmark machine — the v0.5.0 server is
OOM-killed on the 7.7 GB Docker VM even running alone (tapstate/tapstate#495). This script
performs the same join in Postgres and the same normalization as the pipeline's js step
(gen_cve_pipelines.py), and writes the same fields to views.cpe_match.

--check compares against the documents Tapstate did write before it died, field by field.

Usage: python build_cpe_match_python.py [--check]
"""
import re
import sys

import psycopg2
from pymongo import MongoClient, UpdateOne

TRUTHY = ["yes", "y", "true", "1", "V", "affected", "vulnerable", "T"]
FIELDS = ["row_id", "cve", "criteria", "vendor", "product", "version", "vulnerable",
          "version_start_including", "version_start_excluding", "version_end_including", "version_end_excluding"]


def normalize(r, alias_map):
    c = r["criteria"] or ""
    alias = product = None
    if c.startswith("cpe:2.3:"):
        p = c.split(":")
        alias, product = p[3], p[4]
    elif m := re.match(r"^([^/\s]+)/(.+)@([^@]*)$", c):
        alias, product = m[1], m[2]
    elif m := re.match(r"^(\S+) (.+) (\S+)$", c):
        alias, product = m[1], m[2].lower().replace(" ", "_")
    r["vendor"] = None if alias is None else alias_map.get(alias, alias.lower())
    r["product"] = None if product is None else product.lower()
    r["vulnerable"] = str(r.pop("vulnerable_flag")) in TRUTHY
    t = r.pop("version_text")
    ver = t
    if t is not None and t != "*":
        if (m := re.match(r"^build-(\d+)$", t)) and len(m[1]) % 3 == 0:
            ver = ".".join(str(int(m[1][i:i + 3])) for i in range(0, len(m[1]), 3))
        elif (m := re.match(r"^v(.+)$", t)) and "_" in t:
            ver = m[1].replace("_", ".")
        else:
            ver = t.replace(",", ".")
    r["version"] = ver
    return r


def rows():
    con = psycopg2.connect("host=127.0.0.1 port=55432 user=postgres password=secret dbname=cvesrc")
    cur = con.cursor()
    cur.execute("select alias, canonical_vendor from vendor_aliases")
    alias_map = dict(cur.fetchall())
    cur = con.cursor(name="cpe")  # server-side cursor: 585k rows
    cur.itersize = 20000
    cur.execute("""select m.row_id, x.cve, m.criteria, m.vulnerable_flag, v.version_text,
                          v.version_start_inc, v.version_start_exc, v.version_end_inc, v.version_end_exc
                   from cpe_matches m left join xw_cpe_matches x on m.cve_id = x.surface_key
                   left join cpe_version_details v on m.row_id = v.row_id order by m.row_id""")
    cols = ["row_id", "cve", "criteria", "vulnerable_flag", "version_text", "version_start_including",
            "version_start_excluding", "version_end_including", "version_end_excluding"]
    for rec in cur:
        yield normalize(dict(zip(cols, rec)), alias_map)


def main():
    views = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")["views"]
    if "--check" in sys.argv:
        written = {d["row_id"]: d for d in views.cpe_match.find({}, {"_id": 0})}
        diff = 0
        for r in rows():
            d = written.get(r["row_id"])
            if d is None:
                continue
            bad = [f for f in FIELDS if d.get(f) != r.get(f)]
            if bad:
                diff += 1
                if diff <= 5:
                    print("differs", r["row_id"], {f: (d.get(f), r.get(f)) for f in bad})
        print(f"compared {len(written)} Tapstate-written documents: {diff} differ")
        return
    batch, n = [], 0
    for r in rows():
        batch.append(UpdateOne({"row_id": r["row_id"]}, {"$set": r}, upsert=True))
        if len(batch) == 5000:
            views.cpe_match.bulk_write(batch, ordered=False)
            n += len(batch)
            batch = []
    if batch:
        views.cpe_match.bulk_write(batch, ordered=False)
        n += len(batch)
    print(f"wrote {n} cpe_match documents")


if __name__ == "__main__":
    main()
