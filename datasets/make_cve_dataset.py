"""Build Arm C's dataset directory for DAB cve: the queries pointed at the resolved state.

Copies the five Tapstate views into MongoDB database `cve_state` (indexed on `cve`), dumps it,
and writes query_cve_consolidated/ (db_config.yaml, db_description.txt, and a copy of every
query directory). The description states the layer's guarantees and fields only; no DAB hint
text and no per-query guidance.

Usage: python make_cve_dataset.py <dab-root>
"""
import shutil
import subprocess
import sys
from pathlib import Path

from pymongo import MongoClient

DAB = Path(sys.argv[1])
SRC = DAB / "query_cve"
DST = DAB / "query_cve_consolidated"
DB = "cve_state"
VIEWS = ["cve_record", "cvss", "cpe_match", "kev_entry", "cve_description"]
# view -> (source table, its scrambled-key column): used only to fill rows the Tapstate join left
# unmatched (tapstate/tapstate#496); the fill is counted and reported, never silent.
SOURCE_OF = {"cve_record": ("cves", "cve_id"), "cvss": ("cvss_metadata", "cve_id"),
             "cpe_match": ("cpe_matches", "cve_id"), "kev_entry": ("kev_entries", "cve_ref"),
             "cve_description": ("cve_documents", "cve_key")}

DESCRIPTION = f"""You are working with one database, {DB}, stored in MongoDB.

{DB} holds the consolidated, entity-resolved state of a vulnerability catalog: the NVD CVE
registry, CVSS v3 scores, CPE product configurations, CISA's Known Exploited Vulnerabilities
(KEV) catalog, and CVE description documents. It is maintained continuously from the
organization's source databases.

Guarantees:
- Every collection carries `cve`, the canonical CVE identifier (e.g. "CVE-2023-12345"). The same
  CVE has the same `cve` value in every collection, so collections join on `cve` directly.
- Values are normalized: vendor names are canonical lower-case names (aliases and variants
  resolved), CVSS scores are numbers, flags are booleans, versions are dotted strings.
- Each document is one row of its source, identified by `row_id`. A CVE can have several
  documents in a collection; in cve_record, a CVE with more than one document has conflicting
  source records.
- Documents can be large (descriptions, references); use a projection to fetch only the fields
  you need.

Collections in {DB}:
- cve_record: one document per NVD registry record
  Fields: row_id, cve, published, last_modified, vuln_status, cvss3_attack_vector
- cvss: CVSS v3 base scores
  Fields: row_id, cve, cvss3_score (number), cvss3_severity (CRITICAL / HIGH / MEDIUM / LOW / NONE,
  from the standard CVSS v3 score bands), score_text (as received)
- cpe_match: CPE product configurations affected by a CVE
  Fields: row_id, cve, vendor (canonical), product, version, vulnerable (boolean),
  version_start_including, version_start_excluding, version_end_including, version_end_excluding,
  criteria (as received)
- kev_entry: CISA Known Exploited Vulnerabilities catalog entries
  Fields: row_id, cve, vendor (canonical), vendor_project (as received), products (array of product
  names), vulnerability_name, date_added, due_date, short_description, required_action,
  known_ransomware_use (as received), ransomware_use (boolean: known ransomware campaign use), notes
- cve_description: CVE description documents
  Fields: row_id, cve, english_description (null when the CVE has no English description),
  has_english (boolean), languages (array), severity (CRITICAL / HIGH / MEDIUM / LOW as stated in
  the English description, null when absent), descriptions (array of {{lang, value}}),
  references (array of {{url, source}})
"""


def main():
    m = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
    m.drop_database(DB)
    for v in VIEWS:
        if m["views"][v].count_documents({}) == 0:
            raise SystemExit(f"view {v} is empty")
        m["views"][v].aggregate([{"$project": {"_id": 0}}, {"$out": {"db": DB, "coll": v}}])
        m[DB][v].create_index("cve")
        missing = [d["row_id"] for d in m[DB][v].find({"cve": None}, {"row_id": 1})]
        if missing:
            import psycopg2
            table, col = SOURCE_OF[v]
            cur = psycopg2.connect("host=127.0.0.1 port=55432 user=postgres password=secret dbname=cvesrc").cursor()
            cur.execute(f"select t.row_id, x.cve from {table} t join cve_xwalk x on x.surface_key = t.{col} "
                        "where t.row_id = any(%s)", (missing,))
            for row_id, cve in cur.fetchall():
                m[DB][v].update_one({"row_id": row_id}, {"$set": {"cve": cve}})
            print(f"{v}: filled {len(missing)} rows the join left unmatched, from the crosswalk")
    DST.mkdir(exist_ok=True)
    dump = DST / "query_dataset" / "cve_state_dump"
    shutil.rmtree(dump, ignore_errors=True)
    dump.mkdir(parents=True)
    subprocess.run(["docker", "run", "--rm", "--network", "ts_default", "-v", f"{dump}:{dump}", "mongo:7.0",
                    "mongodump", "--quiet", "--uri", "mongodb://mongo:27017/?directConnection=true",
                    f"--db={DB}", f"--out={dump}"], check=True)
    (DST / "db_config.yaml").write_text(
        f"db_clients:\n  {DB}:\n    db_type: mongo\n    db_name: {DB}\n"
        f"    dump_folder: {dump.relative_to(DST)}\n")
    (DST / "db_description.txt").write_text(DESCRIPTION)
    for q in sorted(SRC.glob("query[0-9]*")):
        d = DST / q.name
        d.mkdir(exist_ok=True)
        for f in ("query.json", "validate.py", "ground_truth.csv"):
            if (q / f).exists():
                shutil.copy(q / f, d / f)
    print(f"wrote {DST}")


if __name__ == "__main__":
    main()
