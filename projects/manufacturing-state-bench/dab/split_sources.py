"""Split AdventureWorks into six source systems on three engines and write setup A's DAB dataset.

Writes <dab-root>/query_manufacturing/query_dataset/:
  erp.sql              ERP sales (Postgres mfg_erp): territories, stores, customers, sales_orders,
                       sales_order_lines (sku = product number)
  plm.sql              PLM (Postgres mfg_plm): products, subcategories, categories, models, bom
  costing.sql          costing (Postgres mfg_costing): standard_cost_history, list_price_history, item_prices
  mes.sql              MES (MySQL mfg_mes): work_orders, routings, work_centers, scrap_reasons
                       (part_no = lower case)
  procurement.sql      procurement (MySQL mfg_procurement): vendors, vendor_items, purchase_orders, po_lines,
                       ship_methods (part_ref = product number with '-' replaced by '_')
  mfg_wms_dump/        WMS (MongoDB mfg_wms, on its own server): stock (item = 'AW:' + product number), locations
and query_manufacturing/db_config.yaml and db_description.txt. Then loads the six databases from those
same files. Tapstate reads these databases directly (tapstate/source/).

Every product reference is the product number in that system's format (common.py); integer
ProductIDs appear nowhere. Values are not changed; dates are SQL DATEs. Left out: Person and address
tables (customer names, addresses, credit cards) and TransactionHistory (SOURCE_SPLIT.md §2).

Usage: python split_sources.py <dab-root> [--no-load]
"""
import json
import sys
from pathlib import Path

import bson
import psycopg2
import pymysql
from pymongo import MongoClient

import aw
from common import (COSTING_DB, DATASET, ERP_DB, HERE, MES_DB, MONGO_SOURCES_URI, MYSQL, PG, PLM_DB,
                    PLM_PRODUCT_FIELDS, PO_LINE_FIELDS, PROC_DB,
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
           "mysql": {I: "INT", N: "DECIMAL(19,6)", D: "DATE", S: "VARCHAR(255)"}}
# Postgres sources: the database and file of each system; MySQL likewise
PG_SYSTEMS = {"erp": (ERP_DB, "erp.sql"), "plm": (PLM_DB, "plm.sql"), "costing": (COSTING_DB, "costing.sql")}
MYSQL_SYSTEMS = {"mes": (MES_DB, "mes.sql"), "procurement": (PROC_DB, "procurement.sql")}


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
def sql_literal(v, engine="postgres"):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    v = str(v).replace("'", "''")
    if engine == "mysql":   # a backslash escapes in MySQL string literals
        v = v.replace("\\", "\\\\")
    return "'" + v + "'"


def ddl(cols, pk, engine):
    return ", ".join(f"{c} {SQLTYPE[engine][t]}" for c, t in cols) + f", PRIMARY KEY ({pk})"


def sql_file(title, tables, engine):
    """The system's tables as one SQL script for its engine. Postgres tables get REPLICA IDENTITY FULL,
    so Tapstate's change capture sees whole before-images (MySQL's ROW binlog carries them already)."""
    out = [f"-- {title}. Generated by split_sources.py from Microsoft AdventureWorks (MIT)."]
    out.append("SET client_encoding = 'UTF8';" if engine == "postgres" else "SET NAMES utf8mb4;")
    for name, (cols, _, pk) in tables.items():
        out.append(f"CREATE TABLE {name} ({ddl(cols, pk, engine)});")
        if engine == "postgres":
            out.append(f"ALTER TABLE {name} REPLICA IDENTITY FULL;")
    for name, (_, data, _) in tables.items():
        for i in range(0, len(data), 1000):
            vals = ",\n".join("(" + ", ".join(sql_literal(v, engine) for v in row) + ")" for row in data[i:i + 1000])
            out.append(f"INSERT INTO {name} VALUES\n{vals};")
    return "\n".join(out) + "\n"


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
    db_type: postgres
    db_name: {PLM_DB}
    sql_file: query_dataset/plm.sql
  mes:
    db_type: mysql
    db_name: {MES_DB}
    sql_file: query_dataset/mes.sql
  wms:
    db_type: mongo
    db_name: {WMS_DB}
    dump_folder: query_dataset/{WMS_DB}_dump
    uri: {MONGO_SOURCES_URI}
  procurement:
    db_type: mysql
    db_name: {PROC_DB}
    sql_file: query_dataset/procurement.sql
  costing:
    db_type: postgres
    db_name: {COSTING_DB}
    sql_file: query_dataset/costing.sql
"""
TITLES = {"erp": "ERP sales system", "plm": "PLM system", "costing": "Costing system", "mes": "MES system",
          "procurement": "Procurement system"}


def write_dataset(dab, sy=None):
    sy = sy or systems()
    base = dab / f"query_{DATASET}"
    qd = base / "query_dataset"
    qd.mkdir(parents=True, exist_ok=True)
    for f in ("plm.db", "mes.duckdb", "procurement.duckdb"):   # the earlier layout's files
        (qd / f).unlink(missing_ok=True)
    for system, (_, f) in PG_SYSTEMS.items():
        (qd / f).write_text(sql_file(TITLES[system], sy[system], "postgres"))
    for system, (_, f) in MYSQL_SYSTEMS.items():
        (qd / f).write_text(sql_file(TITLES[system], sy[system], "mysql"))
    write_mongo_dump(qd / f"{WMS_DB}_dump", WMS_DB, sy["wms"])
    (base / "db_config.yaml").write_text(DB_CONFIG)
    (base / "db_description.txt").write_text((HERE / "descriptions" / "setup_a.txt").read_text())
    for system, tables in sy.items():
        for name, t in tables.items():
            print(f"{system:12s} {name:22s} {len(t if isinstance(t, list) else t[1]):7d}")
    return base


def load_server_side(base):
    """(Re)create the six source databases from the files just written, as DAB's own loaders would.
    Tapstate's pipelines read these databases: stop them first (they hold replication slots and
    binlog positions), and rerun them after."""
    qd = base / "query_dataset"
    admin = psycopg2.connect(dbname="postgres", **PG)
    admin.autocommit = True
    cur = admin.cursor()
    for db, f in PG_SYSTEMS.values():
        cur.execute("SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots "
                    "WHERE database = %s AND NOT active", (db,))
        cur.execute(f"DROP DATABASE IF EXISTS {db} WITH (FORCE)")
        cur.execute(f"CREATE DATABASE {db} WITH ENCODING = 'UTF8' LC_COLLATE='C' LC_CTYPE='C' TEMPLATE=template0")
        con = psycopg2.connect(dbname=db, **PG)
        con.autocommit = True
        con.cursor().execute((qd / f).read_text())
        con.close()
    for db, f in MYSQL_SYSTEMS.values():
        con = pymysql.connect(**MYSQL, charset="utf8mb4", autocommit=True,
                              client_flag=pymysql.constants.CLIENT.MULTI_STATEMENTS)
        with con.cursor() as c:
            c.execute(f"DROP DATABASE IF EXISTS {db}")
            c.execute(f"CREATE DATABASE {db} CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin")
            c.execute(f"USE {db}")
            c.execute((qd / f).read_text())
            while c.nextset():
                pass
        con.close()
    m = MongoClient(MONGO_SOURCES_URI)
    m.drop_database(WMS_DB)
    for f in sorted((qd / f"{WMS_DB}_dump" / WMS_DB).glob("*.bson")):
        m[WMS_DB][f.stem].insert_many(bson.decode_all(f.read_bytes()))
    print(f"loaded Postgres {', '.join(d for d, _ in PG_SYSTEMS.values())}; "
          f"MySQL {', '.join(d for d, _ in MYSQL_SYSTEMS.values())}; MongoDB {WMS_DB} ({MONGO_SOURCES_URI})")


def main():
    dab = Path(sys.argv[1]).resolve()
    base = write_dataset(dab)
    if "--no-load" not in sys.argv:
        load_server_side(base)
    print(f"wrote {base}")


if __name__ == "__main__":
    main()
