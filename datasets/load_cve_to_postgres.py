"""Load DAB cve's four stores into one Postgres database (`cvesrc`) for Tapstate, plus the
entity-resolution crosswalk.

Raw tables are copied unchanged except for a surrogate `row_id` primary key (the source tables
have none, and CVE rows are deliberately duplicated). cpe_matches and cpe_version_details were
generated row-for-row in the same order, so row_id aligns them. The Mongo descriptions become
cve_documents(row_id, cve_key, descriptions_json, references_json).

cve_xwalk(surface_key, cve) is the output of the entity-resolution step (cve_decode.py, an
upper-bound resolver built from the dataset's generator): one row per distinct scrambled key.

Usage: python load_cve_to_postgres.py <dab-root>
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import duckdb
import psycopg2
from psycopg2.extras import execute_values
from pymongo import MongoClient

import cve_decode

DAB = Path(sys.argv[1])
QD = DAB / "query_cve" / "query_dataset"
DSN = "host=127.0.0.1 port=55432 user=postgres password=secret"


def pg(db):
    c = psycopg2.connect(f"{DSN} dbname={db}")
    c.autocommit = True
    return c


def create(cur, name, cols, pk, rows):
    cur.execute(f'DROP TABLE IF EXISTS "{name}"')
    cur.execute(f'CREATE TABLE "{name}" ({", ".join(f"{c} {t}" for c, t in cols)}, PRIMARY KEY ({pk}))')
    execute_values(cur, f'INSERT INTO "{name}" VALUES %s', rows, page_size=5000)
    cur.execute(f'ALTER TABLE "{name}" REPLICA IDENTITY FULL')
    print(f"{name:22s} {len(rows):8d} rows")


def main():
    admin = pg("postgres").cursor()
    admin.execute("DROP DATABASE IF EXISTS cvesrc WITH (FORCE)")
    admin.execute("CREATE DATABASE cvesrc")
    cur = pg("cvesrc").cursor()
    keys = set()

    s = sqlite3.connect(QD / "vulns.db")
    rows = [(i,) + r for i, r in enumerate(s.execute(
        "select cve_id, published, last_modified, vuln_status, cvss3_attack_vector from cves"), 1)]
    keys.update(r[1] for r in rows)
    create(cur, "cves", [("row_id", "BIGINT"), ("cve_id", "TEXT"), ("published", "TEXT"),
                         ("last_modified", "TEXT"), ("vuln_status", "TEXT"), ("cvss3_attack_vector", "TEXT")],
           "row_id", rows)
    rows = [(i,) + r for i, r in enumerate(s.execute("select cve_id, score_text from cvss_metadata"), 1)]
    keys.update(r[1] for r in rows)
    create(cur, "cvss_metadata", [("row_id", "BIGINT"), ("cve_id", "TEXT"), ("score_text", "TEXT")], "row_id", rows)

    d = duckdb.connect(str(QD / "cpe.duckdb"), read_only=True)
    rows = [(i,) + tuple(r) for i, r in enumerate(d.execute(
        "select cve_id, criteria, vulnerable_flag from cpe_matches order by rowid").fetchall(), 1)]
    keys.update(r[1] for r in rows)
    create(cur, "cpe_matches", [("row_id", "BIGINT"), ("cve_id", "TEXT"), ("criteria", "TEXT"),
                                ("vulnerable_flag", "TEXT")], "row_id", rows)
    vrows = [(i,) + tuple(r) for i, r in enumerate(d.execute(
        "select cve_id, criteria, version_text, version_start_inc, version_start_exc, version_end_inc, "
        "version_end_exc from cpe_version_details order by rowid").fetchall(), 1)]
    assert all(a[1] == b[1] and a[2] == b[2] for a, b in zip(rows, vrows)), "cpe tables not row-aligned"
    create(cur, "cpe_version_details",
           [("row_id", "BIGINT"), ("cve_id", "TEXT"), ("criteria", "TEXT"), ("version_text", "TEXT"),
            ("version_start_inc", "TEXT"), ("version_start_exc", "TEXT"), ("version_end_inc", "TEXT"),
            ("version_end_exc", "TEXT")], "row_id", vrows)
    create(cur, "vendor_aliases", [("alias", "TEXT"), ("canonical_vendor", "TEXT")], "alias",
           [tuple(r) for r in d.execute("select alias, canonical_vendor from vendor_aliases").fetchall()])

    # kev.sql: run it in a staging database, then copy with a row_id
    admin.execute("DROP DATABASE IF EXISTS cve_stage WITH (FORCE)")
    admin.execute("CREATE DATABASE cve_stage")
    st = pg("cve_stage").cursor()
    st.execute((QD / "kev.sql").read_text())
    st.execute("select cve_ref, vendor_project, products_csv, vulnerability_name, date_added, short_description, "
               "required_action, due_date, known_ransomware_use, notes from kev_entries")
    rows = [(i,) + r for i, r in enumerate(st.fetchall(), 1)]
    keys.update(r[1] for r in rows)
    create(cur, "kev_entries", [("row_id", "BIGINT")] + [(c, "TEXT") for c in (
        "cve_ref", "vendor_project", "products_csv", "vulnerability_name", "date_added", "short_description",
        "required_action", "due_date", "known_ransomware_use", "notes")], "row_id", rows)
    st.execute("select vendor_project, canonical_vendor from kev_vendor_aliases")
    create(cur, "kev_vendor_aliases", [("vendor_project", "TEXT"), ("canonical_vendor", "TEXT")],
           "vendor_project", st.fetchall())
    st.connection.close()
    admin.execute("DROP DATABASE cve_stage WITH (FORCE)")

    m = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")["cve_descriptions"]
    rows = [(i, doc["cve"], json.dumps(doc.get("descriptions") or []), json.dumps(doc.get("references") or []))
            for i, doc in enumerate(m.cve_documents.find({}, {"_id": 0}), 1)]
    keys.update(r[1] for r in rows)
    create(cur, "cve_documents", [("row_id", "BIGINT"), ("cve_key", "TEXT"), ("descriptions_json", "TEXT"),
                                  ("references_json", "TEXT")], "row_id", rows)

    cve_decode.load_generator(DAB)
    xwalk = [(k, cve_decode.decode(k)) for k in sorted(k for k in keys if k)]
    bad = [k for k, v in xwalk if not v or v.startswith("AMBIGUOUS")]
    assert not bad, f"{len(bad)} keys did not resolve"
    create(cur, "cve_xwalk", [("surface_key", "TEXT"), ("cve", "TEXT")], "surface_key", xwalk)


if __name__ == "__main__":
    main()
