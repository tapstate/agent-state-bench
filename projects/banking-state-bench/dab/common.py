"""Shared pieces: paths, the upstream data, the per-system key formats, and the consolidated shape.

Each source system writes the owner's key in its own format (SOURCE_SPLIT.md §2). The forward
functions are what split_sources.py writes; the inverse functions are what the consolidation
(build_consolidated.py, and the Tapstate pipelines) applies to bring them back to canonical keys.
"""
import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
DB_JSON = DATA_DIR / "db.json"

DATASET = "bank"                       # setups A and B: DataAgentBench/query_bank
CONSOLIDATED = "bank_consolidated"     # setup C: DataAgentBench/query_bank_consolidated

# Server-side database names (DAB loads these once and keeps them, DAB_PERSISTENT_DBS=1).
CBS_DB = "bank_cbs"          # Postgres
CRM_DB = "bank_crm"          # MongoDB
STATE_DB = "bank_state"      # MongoDB, setup C

# Connection settings: the same environment variables, and defaults, as the harness (run_arms.env).
PG = dict(host=os.environ.get("PG_HOST", "127.0.0.1"), port=int(os.environ.get("PG_PORT", "55432")),
          user=os.environ.get("PG_USER", "postgres"), password=os.environ.get("PG_PASSWORD", "secret"))
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true")


def load_customers(path=DB_JSON):
    """customer_id -> upstream customer record, in file order."""
    return json.loads(Path(path).read_text())["customers"]


# --- key formats -----------------------------------------------------------------------------
# canonical customer id: CUST_35359B3E      canonical account id: SB1785318658

def suffix(customer_id):
    return customer_id.split("_", 1)[1]


def cards_account_no(account_id):          # cards system: integer, type prefix dropped
    return int(account_id[2:])


def lending_borrower_ref(customer_id):     # lending system: suffix, lower case
    return suffix(customer_id).lower()


def lending_customer_id(borrower_ref):
    return "CUST_" + borrower_ref.upper()


def payments_debit_account(ifsc, account_id):   # payments hub: IFSC-qualified
    return f"{ifsc}/{account_id}"


def payments_account_id(debit_account):
    return debit_account.split("/", 1)[1]


def crm_customer_id(customer_id):          # CRM: hyphen instead of underscore
    return customer_id.replace("_", "-", 1)


def crm_to_customer_id(crm_id):
    return crm_id.replace("-", "_", 1)


def insurance_policyholder_ref(customer_id):   # partner insurer feed
    return "OURB:" + suffix(customer_id)


def insurance_customer_id(ref):
    return "CUST_" + ref.split(":", 1)[1]


# --- the consolidated shape (setup C) ----------------------------------------------------------
MAPS = ["accounts", "deposits", "loans", "cards", "insurance", "requests"]   # id-keyed maps upstream
MAP_ID = {"accounts": "account_id", "deposits": "deposit_id", "loans": "loan_id", "cards": "card_id",
          "insurance": "policy_id", "requests": "request_id"}


def _sorted(rows, key):
    return sorted(rows, key=lambda r: (key(r), json.dumps(r, sort_keys=True)))


def consolidated_doc(customer_id, record):
    """The upstream record as one MongoDB document. Id-keyed maps become arrays; every array is in
    a canonical order, since source systems do not keep the upstream file's list order. Account
    holders keep their order (the first is the primary holder). Values are unchanged.

    This is both setup C's target and the reference it is checked against."""
    lc = record["login_context"]
    doc = {"customer_id": customer_id, "profile": record["profile"],
           "login_context": {"customer_id": lc["customer_id"],
                             **{k: sorted(lc[k]) for k in ("linked_accounts", "linked_products", "linked_cards")}}}
    for k in MAPS:
        doc[k] = [record[k][i] for i in sorted(record[k])]
    doc["accounts"] = [{**a, "transactions": _sorted(a["transactions"], lambda t: t["reference_id"]),
                        "cheques": _sorted(a["cheques"], lambda c: c["cheque_number"])} for a in doc["accounts"]]
    doc["mandates"] = _sorted(record["mandates"], lambda m: m["mandate_id"])
    doc["offers"] = _sorted(record["offers"], lambda o: o["offer_id"])
    return doc
