"""Load the six bank source systems into Postgres for Tapstate, values and key formats unchanged.

Reads the files split_sources.py wrote (setups A/B's query_dataset), not db.json, so Tapstate
consolidates exactly what the agents in setups A and B query. One database, `banksrc`, with one
schema per system: cbs, cards, lending, payments, crm, insurance. Every table gets a primary key
and REPLICA IDENTITY FULL for change capture. Table names are unique across schemas (the CRM's
customers collection is crm.crm_customers), since pipeline steps name tables without a schema.

Usage: python load_bank_to_postgres.py <dab-root>
"""
import sqlite3
import sys
from pathlib import Path

import bson
import duckdb
import psycopg2
from psycopg2.extras import execute_values

from common import CRM_DB, DATASET, PG

DB = "banksrc"

# schema -> table -> (DDL, primary key)
TABLES = {
    "cards": {
        "cards": ("card_id TEXT, account_no BIGINT, card_type TEXT, network TEXT, status TEXT, "
                  "credit_limit DOUBLE PRECISION, available_limit DOUBLE PRECISION", "card_id, account_no"),
        "card_controls": ("card_id TEXT, account_no BIGINT, channel TEXT, region TEXT, enabled INTEGER, "
                          "daily_limit BIGINT", "card_id, account_no, channel, region"),
    },
    "lending": {
        "loans": ("loan_id TEXT, borrower_ref TEXT, loan_type TEXT, outstanding NUMERIC(16,2), rate NUMERIC(6,3), "
                  "emi NUMERIC(16,2), remaining_tenure_months INTEGER, next_due_date TEXT, status TEXT",
                  "loan_id, borrower_ref"),
    },
    "payments": {
        "mandates": ("mandate_id TEXT, debit_account TEXT, payee TEXT, amount BIGINT, frequency TEXT, "
                     "next_debit_date TEXT, status TEXT, mandate_type TEXT", "mandate_id"),
    },
    "insurance": {
        "policies": ("policy_id TEXT, policyholder_ref TEXT, policy_name TEXT, insurer TEXT, sum_assured NUMERIC(16,2), "
                     "premium NUMERIC(16,2), frequency TEXT, status TEXT, next_premium_date TEXT, nominee_name TEXT, "
                     "nominee_relationship TEXT", "policy_id"),
    },
    "crm": {  # crm_customers: the CRM's `customers` collection, renamed so it cannot clash with cbs.customers
        "crm_customers": ("_id TEXT, segment TEXT, profession TEXT, sector TEXT, employer TEXT, marital_status TEXT, "
                      "email TEXT, phone TEXT", "_id"),
        "service_requests": ("_id TEXT, request_id TEXT, category TEXT, description TEXT, related_transaction_id TEXT, "
                             "account_id TEXT, status TEXT, priority TEXT, created_at TEXT, eta TEXT", "_id"),
        "offers": ("_id BIGINT, customer TEXT, offer_id TEXT, type TEXT, title TEXT, detail TEXT, indicative BOOLEAN",
                   "_id"),
    },
}


def table(cur, schema, name, rows):
    ddl, pk = TABLES[schema][name]
    cur.execute(f"CREATE TABLE {schema}.{name} ({ddl}, PRIMARY KEY ({pk}))")
    execute_values(cur, f"INSERT INTO {schema}.{name} VALUES %s", rows, page_size=5000)
    cur.execute(f"ALTER TABLE {schema}.{name} REPLICA IDENTITY FULL")
    print(f"{schema + '.' + name:24s} {len(rows):6d}")


def main():
    qd = Path(sys.argv[1]).resolve() / f"query_{DATASET}" / "query_dataset"
    admin = psycopg2.connect(dbname="postgres", **PG)
    admin.autocommit = True
    admin.cursor().execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    admin.cursor().execute(f"CREATE DATABASE {DB}")
    con = psycopg2.connect(dbname=DB, **PG)
    con.autocommit = True
    cur = con.cursor()
    for schema in ("cbs", *TABLES):
        cur.execute(f"CREATE SCHEMA {schema}")

    # core banking: its own SQL file, as written for setups A/B (keys and REPLICA IDENTITY added here)
    cur.execute("SET search_path TO cbs")
    cur.execute((qd / "cbs.sql").read_text())
    # Dates as ISO text: on Tapstate 0.6.0 a DATE value reaches a pipeline's js step as an opaque
    # object (no fields, prints as "[object Object]"), so it cannot be read there. Same values.
    for t, c in (("accounts", "open_date"), ("transactions", "txn_date"), ("deposits", "start_date"),
                 ("deposits", "maturity_date")):
        cur.execute(f"ALTER TABLE cbs.{t} ALTER COLUMN {c} TYPE TEXT")
    for t in ("customers", "accounts", "account_holders", "transactions", "cheques", "deposits"):
        cur.execute(f"ALTER TABLE cbs.{t} REPLICA IDENTITY FULL")
        cur.execute(f"SELECT count(*) FROM cbs.{t}")
        print(f"{'cbs.' + t:24s} {cur.fetchone()[0]:6d}")
    cur.execute("SET search_path TO public")

    for schema, path in (("cards", "cards.db"), ("payments", "payments.db")):
        s = sqlite3.connect(qd / path)
        for name in TABLES[schema]:
            table(cur, schema, name, list(s.execute(f"SELECT * FROM {name}")))
    for schema, path in (("lending", "lending.duckdb"), ("insurance", "insurance.duckdb")):
        d = duckdb.connect(str(qd / path), read_only=True)
        for name in TABLES[schema]:
            table(cur, schema, name, [tuple(r) for r in d.execute(f"SELECT * FROM {name}").fetchall()])
    dump = qd / f"{CRM_DB}_dump" / CRM_DB
    for name, (ddl, _) in TABLES["crm"].items():
        cols = [c.split()[0] for c in ddl.split(", ")]
        docs = bson.decode_all((dump / f"{name.removeprefix('crm_')}.bson").read_bytes())
        table(cur, "crm", name, [tuple(doc.get(c) for c in cols) for doc in docs])


if __name__ == "__main__":
    main()
