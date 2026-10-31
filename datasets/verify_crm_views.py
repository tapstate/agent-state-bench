"""Check the consolidated MongoDB views against the Postgres source.

For every view: the document count equals the root table's row count, and for every embed
path the total number of embedded elements equals the number of child rows whose cleaned
parent key matches a cleaned parent id (computed independently in SQL). Exit 1 on any mismatch.
"""
import sys
import psycopg2
from pymongo import MongoClient
from gen_crm_pipelines import VIEWS

pg = psycopg2.connect("host=127.0.0.1 port=55432 user=postgres password=secret dbname=crm").cursor()
mdb = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")["views"]
clean = lambda col: f"btrim(ltrim(btrim({col}),'#'))"
bad = 0


def check(label, got, want):
    global bad
    ok = got == want
    bad += not ok
    print(f"{'ok ' if ok else 'BAD'} {label:55s} mongo={got:6d} source={want:6d}")


def walk(view, parent_table, parent_path, embeds):
    for table, fk, path, nested in embeds:
        full = f"{parent_path}.{path}" if parent_path else path
        pg.execute(f'select count(*) from "{table}" c where {clean("c." + fk)} in '
                   f'(select {clean("p.id")} from "{parent_table}" p)')
        want = pg.fetchone()[0]
        parts = full.split(".")
        pipe = [{"$unwind": "$" + ".".join(parts[:i + 1])} for i in range(len(parts))]
        got = next(iter(mdb[view].aggregate(pipe + [{"$count": "n"}])), {"n": 0})["n"]
        check(f"{view}: {full}", got, want)
        walk(view, table, full, nested)


for view, (root, embeds) in VIEWS.items():
    pg.execute(f'select count(*) from "{root}"')
    check(f"{view}: documents", mdb[view].count_documents({}), pg.fetchone()[0])
    walk(view, root, "", embeds)

sys.exit(1 if bad else 0)
