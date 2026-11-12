"""Split AdventureWorks into six source systems and write setup A's DAB dataset.

Writes <dab-root>/query_manufacturing/query_dataset/:
  erp.sql              ERP sales (Postgres mfg_erp): territories, stores, customers, sales_orders,
                       sales_order_lines (sku = product number)
  plm.db               PLM (SQLite): products, subcategories, categories, models, bom
  mes.duckdb           MES (DuckDB): work_orders, routings, work_centers, scrap_reasons (part_no = lower case)
  mfg_wms_dump/        WMS (MongoDB dump): stock (item = 'AW:' + product number), locations
  procurement.duckdb   procurement (DuckDB): vendors, vendor_items, purchase_orders, po_lines, ship_methods
                       (part_ref = product number with '-' replaced by '_')
  costing.sql          costing (Postgres mfg_costing): standard_cost_history, list_price_history, item_prices
and query_manufacturing/db_config.yaml and db_description.txt. Then loads the three server-side
systems (Postgres mfg_erp and mfg_costing, MongoDB mfg_wms) from those same files.

Every product reference is the product number in that system's format (common.py); integer
ProductIDs appear nowhere. Values are not changed. Left out: Person and address tables (customer
names, addresses, credit cards) and TransactionHistory (SOURCE_SPLIT.md §2).

Usage: python split_sources.py <dab-root> [--no-load]
"""
import json
import sqlite3
import sys
from pathlib import Path

import bson
import duckdb
import pandas as pd
import psycopg2
from pymongo import MongoClient

import aw
from common import (COSTING_DB, DATASET, ERP_DB, HERE, MONGO_URI, PG, PLM_PRODUCT_FIELDS, PO_LINE_FIELDS,
                    PURCHASE_ORDER_FIELDS, ROUTING_FIELDS, SALES_LINE_FIELDS, SALES_ORDER_FIELDS, VENDOR_FIELDS,
                    WMS_DB, WORK_ORDER_FIELDS, assert_reversible, mes_part_no, proc_part_ref, rows, wms_item)

I, N, D, S = "int", "num", "date", "text"
# AdventureWorks column -> logical type, for the columns the split keeps
COLTYPE = {
    **{c: I for c in ("MakeFlag", "FinishedGoodsFlag", "SafetyStockLevel", "ReorderPoint", "DaysToManufacture",
                      "OrderQty", "StockedQty", "ScrappedQty", "RevisionNumber", "Status", "OnlineOrderFlag",
                      "SalesPersonID", "ShipMethodID", "SpecialOfferID", "EmployeeID", "CreditRating",
                      "PreferredVendorStatus", "ActiveFlag", "BOMLevel", "OperationSequence", "BusinessEntityID")},
    **{c: N for c in ("Weight", "SubTotal", "TaxAmt", "Freight", "TotalDue", "UnitPrice", "UnitPriceDiscount",
                      "LineTotal", "ReceivedQty", "RejectedQty", "ActualResourceHrs", "PlannedCost",
                      "ActualCost", "PerAssemblyQty")},
    **{c: D for c in ("SellStartDate", "SellEndDate", "DiscontinuedDate", "StartDate", "EndDate", "DueDate",
                      "OrderDate", "ShipDate", "ScheduledStartDate", "ScheduledEndDate", "ActualStartDate",
                      "ActualEndDate")},
}
SQLTYPE = {"postgres": {I: "INTEGER", N: "NUMERIC(19,6)", D: "DATE", S: "TEXT"},
           "sqlite": {I: "INTEGER", N: "REAL", D: "TEXT", S: "TEXT"},
           "duckdb": {I: "INTEGER", N: "DECIMAL(19,6)", D: "DATE", S: "VARCHAR"}}


def fields(pairs):
    return [(k, COLTYPE.get(c, S)) for k, c in pairs]


# --- the six systems: table -> ([(column, type)], rows, primary key) ---------------------------
def systems():
    T = {n: rows(aw.table(n)) for n in (
        "Product", "ProductSubcategory", "ProductCategory", "ProductModel", "BillOfMaterials", "WorkOrder",
        "WorkOrderRouting", "ScrapReason", "Location", "ProductInventory", "Vendor", "ProductVendor",
        "PurchaseOrderHeader", "PurchaseOrderDetail", "ShipMethod", "ProductCostHistory",
        "ProductListPriceHistory", "SalesOrderHeader", "SalesOrderDetail", "Customer", "Store", "SalesTerritory")}
    pn = {p["ProductID"]: p["ProductNumber"] for p in T["Product"]}
    assert_reversible(pn.values())

    def tab(cols, data, pk):
        return ([(c, t) for c, t in cols], [tuple(r) for r in data], pk)

    erp = {
        "territories": tab([("territory_id", I), ("name", S), ("country_region_code", S), ("territory_group", S)],
                           [(t["TerritoryID"], t["Name"], t["CountryRegionCode"], t["Group"])
                            for t in T["SalesTerritory"]], "territory_id"),
        "stores": tab([("store_id", I), ("name", S), ("sales_person_id", I)],
                      [(s["BusinessEntityID"], s["Name"], s["SalesPersonID"]) for s in T["Store"]], "store_id"),
        "customers": tab([("customer_id", I), ("account_number", S), ("store_id", I), ("territory_id", I)],
                         [(c["CustomerID"], c["AccountNumber"], c["StoreID"], c["TerritoryID"]) for c in T["Customer"]],
                         "customer_id"),
        "sales_orders": tab([("sales_order_id", I)] + fields(SALES_ORDER_FIELDS)[:10] + [("customer_id", I),
                                                                                        ("territory_id", I)]
                            + fields(SALES_ORDER_FIELDS)[10:],
                            [(h["SalesOrderID"], *[h[c] for _, c in SALES_ORDER_FIELDS[:10]], h["CustomerID"],
                              h["TerritoryID"], *[h[c] for _, c in SALES_ORDER_FIELDS[10:]])
                             for h in T["SalesOrderHeader"]], "sales_order_id"),
        "sales_order_lines": tab([("sales_order_id", I), ("line_id", I), ("sku", S)] + fields(SALES_LINE_FIELDS),
                                 [(d["SalesOrderID"], d["SalesOrderDetailID"], pn[d["ProductID"]],
                                   *[d[c] for _, c in SALES_LINE_FIELDS]) for d in T["SalesOrderDetail"]], "line_id"),
    }
    plm = {
        "products": tab(fields(PLM_PRODUCT_FIELDS[:15]) + [("subcategory_id", I), ("model_id", I)]
                        + fields(PLM_PRODUCT_FIELDS[15:]),
                        [(*[p[c] for _, c in PLM_PRODUCT_FIELDS[:15]], p["ProductSubcategoryID"], p["ProductModelID"],
                          *[p[c] for _, c in PLM_PRODUCT_FIELDS[15:]]) for p in T["Product"]], "product_number"),
        "subcategories": tab([("subcategory_id", I), ("category_id", I), ("name", S)],
                             [(s["ProductSubcategoryID"], s["ProductCategoryID"], s["Name"])
                              for s in T["ProductSubcategory"]], "subcategory_id"),
        "categories": tab([("category_id", I), ("name", S)],
                          [(c["ProductCategoryID"], c["Name"]) for c in T["ProductCategory"]], "category_id"),
        "models": tab([("model_id", I), ("name", S)], [(m["ProductModelID"], m["Name"]) for m in T["ProductModel"]],
                      "model_id"),
        "bom": tab([("bom_id", I), ("assembly_number", S), ("component_number", S), ("start_date", D),
                    ("end_date", D), ("unit_measure_code", S), ("bom_level", I), ("per_assembly_qty", N)],
                   [(b["BillOfMaterialsID"], pn.get(b["ProductAssemblyID"]), pn[b["ComponentID"]], b["StartDate"],
                     b["EndDate"], b["UnitMeasureCode"], b["BOMLevel"], b["PerAssemblyQty"])
                    for b in T["BillOfMaterials"]], "bom_id"),
    }
    mes = {
        "work_orders": tab([("work_order_id", I), ("part_no", S)] + fields(WORK_ORDER_FIELDS) + [("scrap_reason_id", I)],
                           [(w["WorkOrderID"], mes_part_no(pn[w["ProductID"]]), *[w[c] for _, c in WORK_ORDER_FIELDS],
                             w["ScrapReasonID"]) for w in T["WorkOrder"]], "work_order_id"),
        "routings": tab([("work_order_id", I), ("part_no", S), ("operation_sequence", I), ("work_center_id", I)]
                        + fields(ROUTING_FIELDS),
                        [(r["WorkOrderID"], mes_part_no(pn[r["ProductID"]]), r["OperationSequence"], r["LocationID"],
                          *[r[c] for _, c in ROUTING_FIELDS]) for r in T["WorkOrderRouting"]],
                        "work_order_id, operation_sequence"),
        "work_centers": tab([("work_center_id", I), ("name", S), ("cost_rate", N), ("availability", N)],
                            [(l["LocationID"], l["Name"], l["CostRate"], l["Availability"]) for l in T["Location"]
                             if l["LocationID"] >= 10], "work_center_id"),
        "scrap_reasons": tab([("scrap_reason_id", I), ("name", S)],
                             [(s["ScrapReasonID"], s["Name"]) for s in T["ScrapReason"]], "scrap_reason_id"),
    }
    wms = {
        "stock": [{"_id": i, "item": wms_item(pn[r["ProductID"]]), "location_id": r["LocationID"], "shelf": r["Shelf"],
                   "bin": r["Bin"], "quantity": r["Quantity"]}
                  for i, r in enumerate(sorted(T["ProductInventory"], key=lambda r: (pn[r["ProductID"]],
                                                                                     r["LocationID"])), 1)],
        "locations": [{"_id": l["LocationID"], "name": l["Name"], "cost_rate": l["CostRate"],
                       "availability": l["Availability"]} for l in T["Location"]],
    }
    procurement = {
        "vendors": tab(fields(VENDOR_FIELDS) + [("purchasing_web_service_url", S)],
                       [(*[v[c] for _, c in VENDOR_FIELDS], v["PurchasingWebServiceURL"]) for v in T["Vendor"]],
                       "vendor_id"),
        "vendor_items": tab([("part_ref", S), ("vendor_id", I), ("average_lead_time", I), ("standard_price", N),
                             ("last_receipt_cost", N), ("last_receipt_date", D), ("min_order_qty", I),
                             ("max_order_qty", I), ("on_order_qty", I), ("unit_measure_code", S)],
                            [(proc_part_ref(pn[r["ProductID"]]), r["BusinessEntityID"], r["AverageLeadTime"],
                              r["StandardPrice"], r["LastReceiptCost"], r["LastReceiptDate"], r["MinOrderQty"],
                              r["MaxOrderQty"], r["OnOrderQty"], r["UnitMeasureCode"]) for r in T["ProductVendor"]],
                            "part_ref, vendor_id"),
        "purchase_orders": tab([("purchase_order_id", I)] + fields(PURCHASE_ORDER_FIELDS)[:3]
                               + [("vendor_id", I), ("ship_method_id", I)] + fields(PURCHASE_ORDER_FIELDS)[3:],
                               [(h["PurchaseOrderID"], *[h[c] for _, c in PURCHASE_ORDER_FIELDS[:3]], h["VendorID"],
                                 h["ShipMethodID"], *[h[c] for _, c in PURCHASE_ORDER_FIELDS[3:]])
                                for h in T["PurchaseOrderHeader"]], "purchase_order_id"),
        "po_lines": tab([("purchase_order_id", I), ("po_line_id", I), ("part_ref", S)] + fields(PO_LINE_FIELDS),
                        [(d["PurchaseOrderID"], d["PurchaseOrderDetailID"], proc_part_ref(pn[d["ProductID"]]),
                          *[d[c] for _, c in PO_LINE_FIELDS]) for d in T["PurchaseOrderDetail"]], "po_line_id"),
        "ship_methods": tab([("ship_method_id", I), ("name", S), ("ship_base", N), ("ship_rate", N)],
                            [(s["ShipMethodID"], s["Name"], s["ShipBase"], s["ShipRate"]) for s in T["ShipMethod"]],
                            "ship_method_id"),
    }
    costing = {
        "standard_cost_history": tab([("product_number", S), ("start_date", D), ("end_date", D), ("standard_cost", N)],
                                     [(pn[r["ProductID"]], r["StartDate"], r["EndDate"], r["StandardCost"])
                                      for r in T["ProductCostHistory"]], "product_number, start_date"),
        "list_price_history": tab([("product_number", S), ("start_date", D), ("end_date", D), ("list_price", N)],
                                  [(pn[r["ProductID"]], r["StartDate"], r["EndDate"], r["ListPrice"])
                                   for r in T["ProductListPriceHistory"]], "product_number, start_date"),
        "item_prices": tab([("product_number", S), ("standard_cost", N), ("list_price", N)],
                           [(p["ProductNumber"], p["StandardCost"], p["ListPrice"]) for p in T["Product"]],
                           "product_number"),
    }
    return {"erp": erp, "plm": plm, "mes": mes, "wms": wms, "procurement": procurement, "costing": costing}


# --- writers -----------------------------------------------------------------------------------
def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def ddl(cols, pk, engine):
    return ", ".join(f"{c} {SQLTYPE[engine][t]}" for c, t in cols) + f", PRIMARY KEY ({pk})"


def pg_sql(title, tables):
    out = [f"-- {title}. Generated by split_sources.py from Microsoft AdventureWorks (MIT).",
           "SET client_encoding = 'UTF8';"]
    for name, (cols, _, pk) in tables.items():
        out.append(f"CREATE TABLE {name} ({ddl(cols, pk, 'postgres')});")
    for name, (_, data, _) in tables.items():
        for i in range(0, len(data), 1000):
            vals = ",\n".join("(" + ", ".join(sql_literal(v) for v in row) + ")" for row in data[i:i + 1000])
            out.append(f"INSERT INTO {name} VALUES\n{vals};")
    return "\n".join(out) + "\n"


def write_sqlite(path, tables):
    path.unlink(missing_ok=True)
    con = sqlite3.connect(path)
    for name, (cols, data, pk) in tables.items():
        con.execute(f"CREATE TABLE {name} ({ddl(cols, pk, 'sqlite')})")
        con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * len(cols))})", data)
    con.commit()
    con.close()


def write_duckdb(path, tables):
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    for name, (cols, data, pk) in tables.items():
        con.execute(f"CREATE TABLE {name} ({ddl(cols, pk, 'duckdb')})")
        frame = pd.DataFrame(data, columns=[c for c, _ in cols], dtype=object)  # noqa: F841 (read by duckdb)
        con.execute(f"INSERT INTO {name} SELECT * FROM frame")
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
  erp:
    db_type: postgres
    db_name: {ERP_DB}
    sql_file: query_dataset/erp.sql
  plm:
    db_type: sqlite
    db_path: query_dataset/plm.db
  mes:
    db_type: duckdb
    db_path: query_dataset/mes.duckdb
  wms:
    db_type: mongo
    db_name: {WMS_DB}
    dump_folder: query_dataset/{WMS_DB}_dump
  procurement:
    db_type: duckdb
    db_path: query_dataset/procurement.duckdb
  costing:
    db_type: postgres
    db_name: {COSTING_DB}
    sql_file: query_dataset/costing.sql
"""


def write_dataset(dab, sy=None):
    sy = sy or systems()
    base = dab / f"query_{DATASET}"
    qd = base / "query_dataset"
    qd.mkdir(parents=True, exist_ok=True)
    (qd / "erp.sql").write_text(pg_sql("ERP sales system", sy["erp"]))
    (qd / "costing.sql").write_text(pg_sql("Costing system", sy["costing"]))
    write_sqlite(qd / "plm.db", sy["plm"])
    write_duckdb(qd / "mes.duckdb", sy["mes"])
    write_duckdb(qd / "procurement.duckdb", sy["procurement"])
    write_mongo_dump(qd / f"{WMS_DB}_dump", WMS_DB, sy["wms"])
    (base / "db_config.yaml").write_text(DB_CONFIG)
    (base / "db_description.txt").write_text((HERE / "descriptions" / "setup_a.txt").read_text())
    for system, tables in sy.items():
        for name, t in tables.items():
            print(f"{system:12s} {name:22s} {len(t if isinstance(t, list) else t[1]):7d}")
    return base


def load_server_side(base):
    """Load mfg_erp, mfg_costing and mfg_wms from the files just written, as DAB's own loader would."""
    admin = psycopg2.connect(dbname="postgres", **PG)
    admin.autocommit = True
    cur = admin.cursor()
    for db, f in ((ERP_DB, "erp.sql"), (COSTING_DB, "costing.sql")):
        cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {db} WITH ENCODING = 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE=template0")
        con = psycopg2.connect(dbname=db, **PG)
        con.autocommit = True
        con.cursor().execute((base / "query_dataset" / f).read_text())
        con.close()
    m = MongoClient(MONGO_URI)
    m.drop_database(WMS_DB)
    for f in sorted((base / "query_dataset" / f"{WMS_DB}_dump" / WMS_DB).glob("*.bson")):
        m[WMS_DB][f.stem].insert_many(bson.decode_all(f.read_bytes()))
    print(f"loaded Postgres {ERP_DB}, {COSTING_DB} and MongoDB {WMS_DB}")


def main():
    dab = Path(sys.argv[1]).resolve()
    base = write_dataset(dab)
    if "--no-load" not in sys.argv:
        load_server_side(base)
    print(f"wrote {base}")


if __name__ == "__main__":
    main()
