"""Build setup C: one MongoDB document per customer, consolidated from the six source systems.

Reads only the sources setups A/B query (Postgres bank_cbs, MongoDB bank_crm, and the SQLite and
DuckDB files of query_bank), maps each system's key back to the canonical customer or account id
(common.py), and nests everything under the customer. This is the same logic as the Tapstate
pipeline (tapstate/); setup C comes from Tapstate, and this build is kept as an independent check.

Then checks the result against the upstream record (common.consolidated_doc), document for
document and field for field, and refuses to write anything that differs or is unattached.

Writes MongoDB bank_state.customer, its dump, and <dab-root>/query_bank_consolidated/
(db_config.yaml, db_description.txt; query directories come from make_queries.py).

Usage: python build_consolidated.py <dab-root>
"""
import sqlite3
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import duckdb
import psycopg2
import psycopg2.extras
from pymongo import MongoClient

from common import (CBS_DB, CONSOLIDATED, CRM_DB, DATASET, HERE, MONGO_URI, PG, STATE_DB, consolidated_doc,
                    crm_to_customer_id, insurance_customer_id, lending_customer_id, load_customers,
                    payments_account_id)
from split_sources import write_mongo_dump


def plain(v):
    """Database values back to JSON values: Decimal -> float, date -> 'YYYY-MM-DD'."""
    if isinstance(v, Decimal):
        return float(v)
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return v


def records(cur):
    cols = [c[0] for c in cur.description]
    return [{k: plain(v) for k, v in zip(cols, row)} for row in cur.fetchall()]


def nominee(r):
    name, rel = r.pop("nominee_name"), r.pop("nominee_relationship")
    return None if name is None and rel is None else {"name": name, "relationship": rel}


def read_sources(dab):
    qd = dab / f"query_{DATASET}" / "query_dataset"
    pg = psycopg2.connect(dbname=CBS_DB, **PG).cursor()
    cbs = {}
    for t in ("customers", "accounts", "account_holders", "transactions", "cheques", "deposits"):
        pg.execute(f"SELECT * FROM {t}")
        cbs[t] = records(pg)
    lite = sqlite3.connect(qd / "cards.db")
    lite.row_factory = sqlite3.Row
    cards = {t: [dict(r) for r in lite.execute(f"SELECT * FROM {t}")] for t in ("cards", "card_controls")}
    pay = sqlite3.connect(qd / "payments.db")
    pay.row_factory = sqlite3.Row
    mandates = [dict(r) for r in pay.execute("SELECT * FROM mandates")]
    lend = duckdb.connect(str(qd / "lending.duckdb"), read_only=True)
    loans = records(lend.execute("SELECT * FROM loans"))
    ins = duckdb.connect(str(qd / "insurance.duckdb"), read_only=True)
    policies = records(ins.execute("SELECT * FROM policies"))
    crm = MongoClient(MONGO_URI)[CRM_DB]
    crm_data = {c: list(crm[c].find({}, {"_id": 0} if c != "customers" else None))
                for c in ("customers", "service_requests", "offers")}
    return cbs, cards, mandates, loans, policies, crm_data


def consolidate(cbs, cards, mandates, loans, policies, crm):
    """Source rows -> {customer_id: document}. Raises if any row cannot be attached to an owner."""
    account_owner = {a["account_id"]: a["customer_id"] for a in cbs["accounts"]}
    by_number = {int(a[2:]): a for a in account_owner}          # cards write the account as an integer
    unattached = []

    def owner_of_account(acc, what):
        if acc not in account_owner:
            unattached.append((what, acc))
            return None
        return account_owner[acc]

    docs = {}
    crm_profile = {crm_to_customer_id(c.pop("_id")): c for c in crm["customers"]}
    for c in cbs["customers"]:
        cid = c["customer_id"]
        extra = crm_profile.pop(cid, None)
        if extra is None:
            unattached.append(("crm profile missing", cid))
            extra = {}
        profile = {"name": c["name"], "age": c["age"], "gender": c["gender"], "senior_citizen": c["senior_citizen"],
                   "segment": extra.get("segment"), "profession": extra.get("profession"),
                   "sector": extra.get("sector"), "employer": extra.get("employer"),
                   "marital_status": extra.get("marital_status"), "monthly_income": c["monthly_income"],
                   "kyc_status": c["kyc_status"], "email": extra.get("email"), "phone": extra.get("phone"),
                   "communication_address": {k: c[f"comm_{k}"] for k in ("line1", "line2", "city", "state", "pincode")},
                   "permanent_address": {k: c[f"perm_{k}"] for k in ("line1", "line2", "city", "state", "pincode")}}
        docs[cid] = {"customer_id": cid, "profile": profile, "accounts": [], "deposits": [], "loans": [],
                     "cards": [], "mandates": [], "insurance": [], "requests": [], "offers": []}
    unattached += [("crm profile without customer", k) for k in crm_profile]

    holders, txns, cheques = defaultdict(list), defaultdict(list), defaultdict(list)
    for h in sorted(cbs["account_holders"], key=lambda h: (h["account_id"], h["holder_seq"])):
        holders[h["account_id"]].append(h["holder_name"])
    for t in cbs["transactions"]:
        acc = t.pop("account_id")
        t["date"] = t.pop("txn_date")
        txns[acc].append(t)
    for ch in cbs["cheques"]:
        cheques[ch.pop("account_id")].append(ch)
    for a in cbs["accounts"]:
        cid = a.pop("customer_id")
        a["nominee"] = nominee(a)
        a["holders"] = holders.pop(a["account_id"], [])
        a["transactions"] = txns.pop(a["account_id"], [])
        a["cheques"] = cheques.pop(a["account_id"], [])
        docs[cid]["accounts"].append(a)
    unattached += [("orphan rows", k) for k in list(holders) + list(txns) + list(cheques)]

    for d in cbs["deposits"]:
        d["nominee"] = nominee(d)
        d = {k: v for k, v in d.items() if v is not None}       # FD and RD carry different fields
        cid = owner_of_account(d["payout_account"], "deposit")
        if cid:
            docs[cid]["deposits"].append(d)

    controls = defaultdict(lambda: defaultdict(dict))
    for c in cards["card_controls"]:
        v = {"enabled": bool(c["enabled"])}
        if c["daily_limit"] is not None:
            v["daily_limit"] = c["daily_limit"]
        controls[(c["card_id"], c["account_no"])][c["channel"]][c["region"]] = v
    for k in cards["cards"]:
        acc = by_number.get(k["account_no"])
        if acc is None:
            unattached.append(("card", k["account_no"]))
            continue
        cid = owner_of_account(acc, "card")
        if cid:
            ctl = controls.pop((k["card_id"], k["account_no"]), {})
            docs[cid]["cards"].append({"card_id": k["card_id"], "card_type": k["card_type"], "network": k["network"],
                                       "status": k["status"], "credit_limit": k["credit_limit"],
                                       "available_limit": k["available_limit"], "linked_account": acc,
                                       "controls": {ch: dict(r) for ch, r in ctl.items()}})
    unattached += [("orphan card controls", k) for k in controls]

    for m in mandates:
        acc = payments_account_id(m.pop("debit_account"))
        m["account_id"] = acc
        m["type"] = m.pop("mandate_type")
        cid = owner_of_account(acc, "mandate")
        if cid:
            docs[cid]["mandates"].append(m)
    for l in loans:
        cid = lending_customer_id(l.pop("borrower_ref"))
        if cid in docs:
            docs[cid]["loans"].append(l)
        else:
            unattached.append(("loan", cid))
    for p in policies:
        cid = insurance_customer_id(p.pop("policyholder_ref"))
        p["nominee"] = nominee(p)
        if cid in docs:
            docs[cid]["insurance"].append(p)
        else:
            unattached.append(("policy", cid))
    for r in crm["service_requests"]:
        cid = owner_of_account(r["account_id"], "service request")
        if cid:
            docs[cid]["requests"].append(r)
    for o in crm["offers"]:
        cid = crm_to_customer_id(o.pop("customer"))
        if cid in docs:
            docs[cid]["offers"].append(o)
        else:
            unattached.append(("offer", cid))
    if unattached:
        raise SystemExit(f"{len(unattached)} rows could not be attached, e.g. {unattached[:5]}")

    out = {}
    for cid, d in docs.items():
        rec = {"profile": d["profile"],
               "login_context": {"customer_id": cid,
                                 "linked_accounts": [a["account_id"] for a in d["accounts"]],
                                 "linked_products": [x["deposit_id"] for x in d["deposits"]]
                                 + [x["loan_id"] for x in d["loans"]] + [x["policy_id"] for x in d["insurance"]],
                                 "linked_cards": [x["card_id"] for x in d["cards"]]}}
        for k, idf in (("accounts", "account_id"), ("deposits", "deposit_id"), ("loans", "loan_id"),
                       ("cards", "card_id"), ("insurance", "policy_id"), ("requests", "request_id")):
            rec[k] = {x[idf]: x for x in d[k]}
        rec["mandates"], rec["offers"] = d["mandates"], d["offers"]
        out[cid] = consolidated_doc(cid, rec)
    return out


def diff(a, b, path=""):
    """First difference between two JSON values, or None. Numbers compare by value (0 == 0.0)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'built' if k in a else 'upstream'}"
            d = diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items built, {len(b)} upstream"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    if isinstance(a, bool) != isinstance(b, bool) or a != b:
        return f"{path}: built {a!r}, upstream {b!r}"
    return None


def verify(built, upstream):
    """Every built document must equal the upstream record, and no customer may be missing or extra."""
    problems = [f"missing {c}" for c in upstream if c not in built] + [f"extra {c}" for c in built if c not in upstream]
    for cid, rec in upstream.items():
        if cid in built:
            d = diff(built[cid], consolidated_doc(cid, rec), cid)
            if d:
                problems.append(d)
    return problems


DESCRIPTION = (HERE / "descriptions" / "setup_c.txt")


def main():
    dab = Path(sys.argv[1]).resolve()
    built = consolidate(*read_sources(dab))
    problems = verify(built, load_customers())
    if problems:
        raise SystemExit(f"{len(problems)} differences from upstream, e.g.:\n" + "\n".join(problems[:10]))
    print(f"verified: {len(built)} customer documents equal the upstream records field for field")

    install(dab, built)


def install(dab, built):
    """Make verified documents setup C: MongoDB bank_state.customer, its dump, and the dataset's
    db_config.yaml and db_description.txt. Used by this build and by verify_tapstate.py --install."""
    rows = [{"_id": cid, **d} for cid, d in built.items()]
    m = MongoClient(MONGO_URI)
    m.drop_database(STATE_DB)
    m[STATE_DB]["customer"].insert_many(rows)
    m[STATE_DB]["customer"].create_index("profile.segment")
    base = dab / f"query_{CONSOLIDATED}"
    write_mongo_dump(base / "query_dataset" / f"{STATE_DB}_dump", STATE_DB, {"customer": rows})
    (base / "db_config.yaml").write_text(
        f"db_clients:\n  bank_state:\n    db_type: mongo\n    db_name: {STATE_DB}\n"
        f"    dump_folder: query_dataset/{STATE_DB}_dump\n")
    (base / "db_description.txt").write_text(DESCRIPTION.read_text())
    print(f"wrote MongoDB {STATE_DB}.customer and {base}")


if __name__ == "__main__":
    main()
