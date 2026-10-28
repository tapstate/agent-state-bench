"""Split the upstream bank (db.json) into six source systems and write setups A/B's DAB dataset.

Writes <dab-root>/query_bank/query_dataset/:
  cbs.sql            core banking (Postgres): customers, accounts, account_holders, transactions,
                     cheques, deposits
  cards.db           cards (SQLite): cards, card_controls
  lending.duckdb     lending (DuckDB): loans
  payments.db        payments hub (SQLite): mandates
  bank_crm_dump/     CRM (MongoDB dump, mongorestore layout): customers, service_requests, offers
  insurance.duckdb   bancassurance feed (DuckDB): policies
and query_bank/db_config.yaml. Then loads the two server-side systems (Postgres bank_cbs, MongoDB
bank_crm) from those same files, which DAB keeps between runs (DAB_PERSISTENT_DBS=1).

Each system writes the owner's key in its own format (common.py). Values are not changed.
Card, deposit and loan ids are not unique upstream, so those tables key on (id, owner).

Usage: python split_sources.py <dab-root> [--no-load]
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

import bson
import duckdb
import psycopg2
from pymongo import MongoClient

from common import (CBS_DB, CRM_DB, DATASET, HERE, MONGO_URI, PG, cards_account_no, crm_customer_id,
                    insurance_policyholder_ref, lending_borrower_ref, load_customers, payments_debit_account)


# --- rows per system -------------------------------------------------------------------------
def nominee(n):
    return (n["name"], n["relationship"]) if n else (None, None)


def addr(a):
    return (a["line1"], a["line2"], a["city"], a["state"], a["pincode"])


def rows(customers):
    out = {k: [] for k in ("customers", "accounts", "account_holders", "transactions", "cheques", "deposits",
                           "cards", "card_controls", "loans", "mandates", "crm_customers",
                           "service_requests", "offers", "policies")}
    for cid, c in customers.items():
        p = c["profile"]
        out["customers"].append((cid, p["name"], p["age"], p["gender"], p["senior_citizen"], p["kyc_status"],
                                 p["monthly_income"], *addr(p["communication_address"]),
                                 *addr(p["permanent_address"])))
        out["crm_customers"].append({"_id": crm_customer_id(cid), "segment": p["segment"],
                                     "profession": p["profession"], "sector": p["sector"],
                                     "employer": p["employer"], "marital_status": p["marital_status"],
                                     "email": p["email"], "phone": p["phone"]})
        ifsc = {}
        for a in c["accounts"].values():
            ifsc[a["account_id"]] = a["ifsc"]
            out["accounts"].append((a["account_id"], cid, a["account_type"], a["status"], a["ifsc"],
                                    a["home_branch"], *nominee(a["nominee"]), a["open_date"],
                                    a["available_balance"], a["hold_amount"], a["currency"]))
            out["account_holders"] += [(a["account_id"], i, h) for i, h in enumerate(a["holders"], 1)]
            out["transactions"] += [(t["reference_id"], a["account_id"], t["date"], t["description"], t["amount"],
                                     t["direction"], t["channel"], t["balance_after"]) for t in a["transactions"]]
            out["cheques"] += [(a["account_id"], ch["cheque_number"], ch["status"]) for ch in a["cheques"]]
        for d in c["deposits"].values():
            out["deposits"].append((d["deposit_id"], d["payout_account"], d["kind"], d.get("principal"), d["rate"],
                                    d["tenure_months"], d["start_date"], d["maturity_date"], d["maturity_amount"],
                                    d.get("payout_type"), d["maturity_instruction"], d["status"],
                                    *nominee(d["nominee"]), d.get("monthly_installment"),
                                    d.get("installments_paid"), d.get("total_deposited"), d.get("source_account")))
        for k in c["cards"].values():
            no = cards_account_no(k["linked_account"])
            out["cards"].append((k["card_id"], no, k["card_type"], k["network"], k["status"], k["credit_limit"],
                                 k["available_limit"]))
            for channel, regions in k["controls"].items():
                for region, v in regions.items():
                    out["card_controls"].append((k["card_id"], no, channel, region, int(v["enabled"]),
                                                 v.get("daily_limit")))
        for l in c["loans"].values():
            out["loans"].append((l["loan_id"], lending_borrower_ref(cid), l["loan_type"], l["outstanding"], l["rate"],
                                 l["emi"], l["remaining_tenure_months"], l["next_due_date"], l["status"]))
        for m in c["mandates"]:
            out["mandates"].append((m["mandate_id"], payments_debit_account(ifsc[m["account_id"]], m["account_id"]),
                                    m["payee"], m["amount"], m["frequency"], m["next_debit_date"], m["status"],
                                    m["type"]))
        for r in c["requests"].values():
            out["service_requests"].append({"_id": r["request_id"], **r})
        for o in c["offers"]:
            out["offers"].append({"customer": crm_customer_id(cid), **o})
        for p_ in c["insurance"].values():
            out["policies"].append((p_["policy_id"], insurance_policyholder_ref(cid), p_["policy_name"], p_["insurer"],
                                    p_["sum_assured"], p_["premium"], p_["frequency"], p_["status"],
                                    p_["next_premium_date"], *nominee(p_["nominee"])))
    for i, o in enumerate(out["offers"], 1):  # deterministic _id, so the dump is reproducible
        o["_id"] = i
    return out


# --- core banking: Postgres SQL file ---------------------------------------------------------
CBS_TABLES = {
    "customers": ("customer_id TEXT PRIMARY KEY, name TEXT, age INTEGER, gender TEXT, senior_citizen BOOLEAN, "
                  "kyc_status TEXT, monthly_income BIGINT, comm_line1 TEXT, comm_line2 TEXT, comm_city TEXT, "
                  "comm_state TEXT, comm_pincode TEXT, perm_line1 TEXT, perm_line2 TEXT, perm_city TEXT, "
                  "perm_state TEXT, perm_pincode TEXT"),
    "accounts": ("account_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers, account_type TEXT, "
                 "status TEXT, ifsc TEXT, home_branch TEXT, nominee_name TEXT, nominee_relationship TEXT, "
                 "open_date DATE, available_balance NUMERIC(16,2), hold_amount NUMERIC(16,2), currency TEXT"),
    "account_holders": ("account_id TEXT REFERENCES accounts, holder_seq INTEGER, holder_name TEXT, "
                        "PRIMARY KEY (account_id, holder_seq)"),
    "transactions": ("reference_id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts, txn_date DATE, "
                     "description TEXT, amount NUMERIC(16,2), direction TEXT, channel TEXT, "
                     "balance_after NUMERIC(16,2)"),
    "cheques": ("account_id TEXT REFERENCES accounts, cheque_number TEXT, status TEXT, "
                "PRIMARY KEY (account_id, cheque_number)"),
    "deposits": ("deposit_id TEXT, payout_account TEXT REFERENCES accounts, kind TEXT, principal NUMERIC(16,2), "
                 "rate NUMERIC(6,3), tenure_months INTEGER, start_date DATE, maturity_date DATE, "
                 "maturity_amount NUMERIC(16,2), payout_type TEXT, maturity_instruction TEXT, status TEXT, "
                 "nominee_name TEXT, nominee_relationship TEXT, monthly_installment NUMERIC(16,2), "
                 "installments_paid INTEGER, total_deposited NUMERIC(16,2), source_account TEXT, "
                 "PRIMARY KEY (deposit_id, payout_account)"),
}


def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def cbs_sql(r):
    out = ["-- Core banking system (CBS). Generated by split_sources.py from NPCI tau-indian-banking db.json.",
           "SET client_encoding = 'UTF8';"]
    for name, cols in CBS_TABLES.items():
        out.append(f"CREATE TABLE {name} ({cols});")
    for name in CBS_TABLES:
        data = r[name]
        for i in range(0, len(data), 500):
            vals = ",\n".join("(" + ", ".join(sql_literal(v) for v in row) + ")" for row in data[i:i + 500])
            out.append(f"INSERT INTO {name} VALUES\n{vals};")
    return "\n".join(out) + "\n"


# --- file-backed systems ---------------------------------------------------------------------
def write_sqlite(path, tables):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    for name, (ddl, data) in tables.items():
        con.execute(f"CREATE TABLE {name} ({ddl})")
        con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(data[0]))})", data)
    con.commit()
    con.close()


def write_duckdb(path, tables):
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    for name, (ddl, data) in tables.items():
        con.execute(f"CREATE TABLE {name} ({ddl})")
        con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(data[0]))})", data)
    con.close()


def write_mongo_dump(dump, db, collections):
    """mongodump's on-disk layout (<dump>/<db>/<coll>.bson + .metadata.json), written directly."""
    d = dump / db
    d.mkdir(parents=True, exist_ok=True)
    for f in d.iterdir():
        f.unlink()
    for name, docs in collections.items():
        (d / f"{name}.bson").write_bytes(b"".join(bson.encode(doc) for doc in docs))
        (d / f"{name}.metadata.json").write_text(json.dumps(
            {"indexes": [{"v": 2, "key": {"_id": 1}, "name": "_id_"}], "collectionName": name, "type": "collection"}))


DB_CONFIG = f"""db_clients:
  core_banking:
    db_type: postgres
    db_name: {CBS_DB}
    sql_file: query_dataset/cbs.sql
  cards:
    db_type: sqlite
    db_path: query_dataset/cards.db
  lending:
    db_type: duckdb
    db_path: query_dataset/lending.duckdb
  payments:
    db_type: sqlite
    db_path: query_dataset/payments.db
  crm:
    db_type: mongo
    db_name: {CRM_DB}
    dump_folder: query_dataset/{CRM_DB}_dump
  insurance:
    db_type: duckdb
    db_path: query_dataset/insurance.duckdb
"""


# file -> table -> DDL, for the SQLite and DuckDB systems
FILE_TABLES = {
    "cards.db": {
        "cards": ("card_id TEXT, account_no INTEGER, card_type TEXT, network TEXT, status TEXT, credit_limit REAL, "
                  "available_limit REAL, PRIMARY KEY (card_id, account_no)"),
        "card_controls": ("card_id TEXT, account_no INTEGER, channel TEXT, region TEXT, enabled INTEGER, "
                          "daily_limit INTEGER, PRIMARY KEY (card_id, account_no, channel, region)"),
    },
    "lending.duckdb": {
        "loans": ("loan_id VARCHAR, borrower_ref VARCHAR, loan_type VARCHAR, outstanding DECIMAL(16,2), "
                  "rate DECIMAL(6,3), emi DECIMAL(16,2), remaining_tenure_months INTEGER, next_due_date DATE, "
                  "status VARCHAR, PRIMARY KEY (loan_id, borrower_ref)"),
    },
    "payments.db": {
        "mandates": ("mandate_id TEXT PRIMARY KEY, debit_account TEXT, payee TEXT, amount INTEGER, frequency TEXT, "
                     "next_debit_date TEXT, status TEXT, mandate_type TEXT"),
    },
    "insurance.duckdb": {
        "policies": ("policy_id VARCHAR PRIMARY KEY, policyholder_ref VARCHAR, policy_name VARCHAR, "
                     "insurer VARCHAR, sum_assured DECIMAL(16,2), premium DECIMAL(16,2), frequency VARCHAR, "
                     "status VARCHAR, next_premium_date DATE, nominee_name VARCHAR, nominee_relationship VARCHAR"),
    },
}


def columns(ddl):
    """Column names of a CREATE TABLE body, skipping table constraints."""
    parts = re.split(r",\s*(?![^()]*\))", ddl)
    return [p.split()[0] for p in parts if not p.strip().upper().startswith("PRIMARY KEY")]


def write_dataset(dab, customers):
    r = rows(customers)
    base = dab / f"query_{DATASET}"
    qd = base / "query_dataset"
    qd.mkdir(parents=True, exist_ok=True)
    (qd / "cbs.sql").write_text(cbs_sql(r))
    for f, tables in FILE_TABLES.items():
        write = write_sqlite if f.endswith(".db") else write_duckdb
        write(qd / f, {name: (ddl, r[name]) for name, ddl in tables.items()})
    write_mongo_dump(qd / f"{CRM_DB}_dump", CRM_DB, {
        "customers": r["crm_customers"], "service_requests": r["service_requests"], "offers": r["offers"]})
    (base / "db_config.yaml").write_text(DB_CONFIG)
    (base / "db_description.txt").write_text((HERE / "descriptions" / "setup_a.txt").read_text())
    (base / "db_description_withhint.txt").write_text((HERE / "descriptions" / "setup_b_hints.txt").read_text())
    for k in ("customers", "accounts", "transactions", "deposits", "cards", "card_controls", "loans", "mandates",
              "crm_customers", "service_requests", "offers", "policies"):
        print(f"{k:18s} {len(r[k]):6d}")
    return base


def load_server_side(base):
    """Load bank_cbs and bank_crm from the files just written, as DAB's own loader would."""
    admin = psycopg2.connect(dbname="postgres", **PG)
    admin.autocommit = True
    cur = admin.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {CBS_DB} WITH (FORCE)")
    cur.execute(f"CREATE DATABASE {CBS_DB} WITH ENCODING = 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE=template0")
    con = psycopg2.connect(dbname=CBS_DB, **PG)
    con.autocommit = True
    con.cursor().execute((base / "query_dataset" / "cbs.sql").read_text())
    m = MongoClient(MONGO_URI)
    m.drop_database(CRM_DB)
    for f in sorted((base / "query_dataset" / f"{CRM_DB}_dump" / CRM_DB).glob("*.bson")):
        docs = bson.decode_all(f.read_bytes())
        m[CRM_DB][f.stem].insert_many(docs)
    print(f"loaded Postgres {CBS_DB} and MongoDB {CRM_DB}")


def main():
    dab = Path(sys.argv[1]).resolve()
    base = write_dataset(dab, load_customers())
    if "--no-load" not in sys.argv:
        load_server_side(base)
    print(f"wrote {base}")


if __name__ == "__main__":
    main()
