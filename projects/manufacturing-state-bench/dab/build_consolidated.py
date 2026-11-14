"""Build setup C in Python, from the six source systems: the same consolidation as the Tapstate pipelines.

Reads only the sources setup A queries (Postgres mfg_erp, mfg_plm and mfg_costing, MySQL mfg_mes and
mfg_procurement, MongoDB mfg_wms), maps each system's product key back to the
canonical product number (common.py), and assembles the four collections of setup C. Then checks
the result against common.reference_state(), built from the raw AdventureWorks tables by their
integer ids, document for document and field for field, and refuses anything that differs or
cannot be attached.

Setup C itself is Tapstate's output (verify_tapstate.py --install); this build is the independent
check of the pipelines' logic. With --install it writes its own documents as setup C instead.

Usage: python build_consolidated.py <dab-root> [--install]
"""
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import psycopg2
import pymysql
from pymongo import MongoClient

from common import (COLLECTIONS, CONSOLIDATED, COSTING_DB, ERP_DB, HERE, ID, MES_DB, MONGO_SOURCES_URI, MONGO_URI,
                    MYSQL, PG, PLM_DB, PROC_DB, bson_dates,
                    PLM_PRODUCT_FIELDS, PO_LINE_FIELDS, PURCHASE_ORDER_FIELDS, ROUTING_FIELDS, SALES_LINE_FIELDS,
                    SALES_ORDER_FIELDS, STATE_DB, VENDOR_FIELDS, WMS_DB, WORK_ORDER_FIELDS, canonical, mes_to_pn,
                    proc_to_pn, reference_state, verify, wms_to_pn)


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


def read_sources():
    src = {}
    for db, tables in ((ERP_DB, ("territories", "stores", "customers", "sales_orders", "sales_order_lines")),
                       (PLM_DB, ("products", "subcategories", "categories", "models", "bom")),
                       (COSTING_DB, ("standard_cost_history", "list_price_history", "item_prices"))):
        cur = psycopg2.connect(dbname=db, **PG).cursor()
        for t in tables:
            cur.execute(f"SELECT * FROM {t}")
            src[t] = records(cur)
    for db, tables in ((MES_DB, ("work_orders", "routings", "work_centers", "scrap_reasons")),
                       (PROC_DB, ("vendors", "vendor_items", "purchase_orders", "po_lines", "ship_methods"))):
        cur = pymysql.connect(database=db, **MYSQL).cursor()
        for t in tables:
            cur.execute(f"SELECT * FROM {t}")
            src[t] = records(cur)
    wms = MongoClient(MONGO_SOURCES_URI)[WMS_DB]
    src["stock"] = list(wms["stock"].find({}))
    src["locations"] = list(wms["locations"].find({}))
    return src


def consolidate(s):
    """Source rows -> {collection: {id: document}}. Raises if any row cannot be attached."""
    unattached = []
    products = {p["product_number"]: p for p in s["products"]}

    def owner(pn, what):
        if pn not in products:
            unattached.append((what, pn))
            return None
        return pn

    sub = {r["subcategory_id"]: r for r in s["subcategories"]}
    cat = {r["category_id"]: r for r in s["categories"]}
    model = {r["model_id"]: r for r in s["models"]}
    loc = {r["_id"]: r for r in s["locations"]}
    vendor = {r["vendor_id"]: r for r in s["vendors"]}
    prices = {r["product_number"]: r for r in s["item_prices"]}
    by = defaultdict(lambda: defaultdict(list))
    for r in s["standard_cost_history"]:
        if owner(r["product_number"], "cost history"):
            by["cost"][r["product_number"]].append({k: r[k] for k in ("start_date", "end_date", "standard_cost")})
    for r in s["list_price_history"]:
        if owner(r["product_number"], "price history"):
            by["price"][r["product_number"]].append({k: r[k] for k in ("start_date", "end_date", "list_price")})
    for r in s["bom"]:
        if r["assembly_number"] is not None and owner(r["assembly_number"], "bom"):
            by["bom"][r["assembly_number"]].append({k: r[k] for k in ("component_number", "start_date", "end_date",
                                                                      "unit_measure_code", "bom_level",
                                                                      "per_assembly_qty")})
    for r in s["stock"]:
        pn = owner(wms_to_pn(r["item"]), "stock")
        if pn:
            by["inv"][pn].append({"location": {"location_id": r["location_id"], "name": loc[r["location_id"]]["name"]},
                                  "shelf": r["shelf"], "bin": r["bin"], "quantity": r["quantity"]})
    for r in s["vendor_items"]:
        pn = owner(proc_to_pn(r["part_ref"]), "vendor item")
        if pn:
            by["ven"][pn].append({"vendor": {k: vendor[r["vendor_id"]][k] for k, _ in VENDOR_FIELDS},
                                  **{k: r[k] for k in ("average_lead_time", "standard_price", "last_receipt_cost",
                                                       "last_receipt_date", "min_order_qty", "max_order_qty",
                                                       "on_order_qty", "unit_measure_code")}})
    out = {c: {} for c in COLLECTIONS}
    ref = {}
    for pn, p in products.items():
        sc = sub.get(p["subcategory_id"])
        m = model.get(p["model_id"])
        ip = prices.get(pn) or {}
        out["products"][pn] = {
            **{k: p[k] for k, _ in PLM_PRODUCT_FIELDS},
            "standard_cost": ip.get("standard_cost"), "list_price": ip.get("list_price"),
            "subcategory": None if sc is None else {
                "subcategory_id": sc["subcategory_id"], "name": sc["name"],
                "category": {"category_id": sc["category_id"], "name": cat[sc["category_id"]]["name"]}},
            "model": None if m is None else {"model_id": m["model_id"], "name": m["name"]},
            "standard_cost_history": by["cost"][pn], "list_price_history": by["price"][pn], "bom": by["bom"][pn],
            "inventory": by["inv"][pn], "vendors": by["ven"][pn]}
        ref[pn] = {"product_number": pn, "name": p["name"], "subcategory": None if sc is None else sc["name"],
                   "category": None if sc is None else cat[sc["category_id"]]["name"]}

    centers = {r["work_center_id"]: r for r in s["work_centers"]}
    scrap = {r["scrap_reason_id"]: r for r in s["scrap_reasons"]}
    routings = defaultdict(list)
    for r in s["routings"]:
        routings[r["work_order_id"]].append({
            "operation_sequence": r["operation_sequence"],
            "work_center": {"work_center_id": r["work_center_id"], "name": centers[r["work_center_id"]]["name"]},
            **{k: r[k] for k, _ in ROUTING_FIELDS}})
    for w in s["work_orders"]:
        pn = owner(mes_to_pn(w["part_no"]), "work order")
        if pn:
            sr = scrap.get(w["scrap_reason_id"])
            out["work_orders"][w["work_order_id"]] = {
                "work_order_id": w["work_order_id"], "product": ref[pn], **{k: w[k] for k, _ in WORK_ORDER_FIELDS},
                "scrap_reason": None if sr is None else {"scrap_reason_id": sr["scrap_reason_id"], "name": sr["name"]},
                "routings": routings.pop(w["work_order_id"], [])}
    unattached += [("routing without work order", k) for k in routings]

    lines = defaultdict(list)
    for d in s["sales_order_lines"]:
        pn = owner(d["sku"], "sales line")
        if pn:
            lines[d["sales_order_id"]].append({"line_id": d["line_id"], "product": ref[pn],
                                               **{k: d[k] for k, _ in SALES_LINE_FIELDS}})
    store = {r["store_id"]: r for r in s["stores"]}
    cust = {r["customer_id"]: r for r in s["customers"]}
    terr = {r["territory_id"]: r for r in s["territories"]}
    for h in s["sales_orders"]:
        c = cust[h["customer_id"]]
        st = store.get(c["store_id"])
        t = terr[h["territory_id"]]
        out["sales_orders"][h["sales_order_id"]] = {
            "sales_order_id": h["sales_order_id"], **{k: h[k] for k, _ in SALES_ORDER_FIELDS},
            "customer": {"customer_id": c["customer_id"], "account_number": c["account_number"],
                         "store": None if st is None else {"store_id": st["store_id"], "name": st["name"]}},
            "territory": {"territory_id": t["territory_id"], "name": t["name"],
                          "country_region_code": t["country_region_code"], "group": t["territory_group"]},
            "lines": lines.pop(h["sales_order_id"], [])}
    unattached += [("sales lines without order", k) for k in lines]

    po_lines = defaultdict(list)
    for d in s["po_lines"]:
        pn = owner(proc_to_pn(d["part_ref"]), "purchase line")
        if pn:
            po_lines[d["purchase_order_id"]].append({"po_line_id": d["po_line_id"], "product": ref[pn],
                                                     **{k: d[k] for k, _ in PO_LINE_FIELDS}})
    ship = {r["ship_method_id"]: r for r in s["ship_methods"]}
    for h in s["purchase_orders"]:
        out["purchase_orders"][h["purchase_order_id"]] = {
            "purchase_order_id": h["purchase_order_id"], **{k: h[k] for k, _ in PURCHASE_ORDER_FIELDS},
            "vendor": {k: vendor[h["vendor_id"]][k] for k, _ in VENDOR_FIELDS},
            "ship_method": {"ship_method_id": h["ship_method_id"], "name": ship[h["ship_method_id"]]["name"]},
            "lines": po_lines.pop(h["purchase_order_id"], [])}
    unattached += [("purchase lines without order", k) for k in po_lines]
    if unattached:
        raise SystemExit(f"{len(unattached)} rows could not be attached, e.g. {unattached[:5]}")
    return {c: {k: canonical(c, d) for k, d in docs.items()} for c, docs in out.items()}


DESCRIPTION = HERE / "descriptions" / "setup_c.txt"


def install(dab, built):
    """Make verified documents setup C: MongoDB mfg_state (one collection per view), its dump, and the
    dataset's db_config.yaml and db_description.txt. Used by this build and by verify_tapstate.py --install.
    Dates are stored as BSON dates, as Tapstate writes the sources' SQL DATEs (common.bson_dates)."""
    from split_sources import write_mongo_dump
    m = MongoClient(MONGO_URI)
    m.drop_database(STATE_DB)
    colls = {}
    for c in COLLECTIONS:
        colls[c] = [bson_dates({"_id": k, **d}) for k, d in sorted(built[c].items())]
        m[STATE_DB][c].insert_many(colls[c])
    base = dab / f"query_{CONSOLIDATED}"
    write_mongo_dump(base / "query_dataset" / f"{STATE_DB}_dump", STATE_DB, colls)
    (base / "db_config.yaml").write_text(
        f"db_clients:\n  {STATE_DB}:\n    db_type: mongo\n    db_name: {STATE_DB}\n"
        f"    dump_folder: query_dataset/{STATE_DB}_dump\n")
    (base / "db_description.txt").write_text(DESCRIPTION.read_text())
    print(f"wrote MongoDB {STATE_DB} ({', '.join(f'{c} {len(v)}' for c, v in colls.items())}) and {base}")


def main():
    dab = Path(sys.argv[1]).resolve()
    built = consolidate(read_sources())
    problems = verify(built, reference_state())
    if problems:
        raise SystemExit(f"{len(problems)} differences from the reference, e.g.:\n" + "\n".join(problems[:10]))
    print("verified: " + ", ".join(f"{len(built[c])} {c}" for c in COLLECTIONS)
          + " equal the reference built from the raw tables, field for field")
    if "--install" in sys.argv:
        install(dab, built)


if __name__ == "__main__":
    assert set(ID) == set(COLLECTIONS)
    main()
