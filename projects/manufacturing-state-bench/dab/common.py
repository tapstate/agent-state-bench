"""Shared pieces: paths, database names, the per-system product key formats, and setup C's shape.

Each source system writes the product key, AdventureWorks' ProductNumber (e.g. BK-R93R-62), in its
own format (SOURCE_SPLIT.md §2). The forward functions are what split_sources.py writes; the
inverse functions are what the consolidation (build_consolidated.py and the Tapstate pipelines)
applies to bring them back. Upstream product numbers contain no lower-case letter, '_' or ':',
so every format is reversible (checked by assert_reversible).

reference_state() builds setup C's target documents straight from the raw AdventureWorks tables,
by their integer ids, without the split sources: it is what both the Python build and Tapstate's
output are checked against.
"""
import datetime
import json
import math
import os
from collections import defaultdict

import aw

HERE = aw.HERE
DATASET = "manufacturing"                       # setup A: DataAgentBench/query_manufacturing
CONSOLIDATED = "manufacturing_consolidated"     # setup C: DataAgentBench/query_manufacturing_consolidated

# The six source databases, on three engines (stack/docker-compose.yml). Tapstate reads these same
# databases; DAB loads them once and keeps them (DAB_PERSISTENT_DBS=1).
ERP_DB = "mfg_erp"            # Postgres
PLM_DB = "mfg_plm"            # Postgres
COSTING_DB = "mfg_costing"    # Postgres
MES_DB = "mfg_mes"            # MySQL
PROC_DB = "mfg_procurement"   # MySQL
WMS_DB = "mfg_wms"            # MongoDB (mongo-sources, its own server)
STATE_DB = "mfg_state"        # MongoDB (Tapstate's store), setup C

PG = dict(host=os.environ.get("PG_HOST", "127.0.0.1"), port=int(os.environ.get("PG_PORT", "55432")),
          user=os.environ.get("PG_USER", "postgres"), password=os.environ.get("PG_PASSWORD", "secret"))
MYSQL = dict(host=os.environ.get("MYSQL_HOST", "127.0.0.1"), port=int(os.environ.get("MYSQL_PORT", "33306")),
             user=os.environ.get("MYSQL_USER", "root"), password=os.environ.get("MYSQL_PASSWORD", "secret"))
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true")
MONGO_SOURCES_URI = os.environ.get("MONGO_SOURCES_URI", "mongodb://127.0.0.1:27018/?directConnection=true")


# --- key formats -----------------------------------------------------------------------------
# canonical: BK-R93R-62 (ERP sku, PLM, costing)
def mes_part_no(pn):              # MES: lower case
    return pn.lower()


def mes_to_pn(part_no):
    return part_no.upper()


def wms_item(pn):                 # WMS: company-prefixed item code
    return "AW:" + pn


def wms_to_pn(item):
    return item.split(":", 1)[1]


def proc_part_ref(pn):            # procurement: '-' replaced by '_'
    return pn.replace("-", "_")


def proc_to_pn(ref):
    return ref.replace("_", "-")


def assert_reversible(numbers):
    numbers = list(numbers)
    assert len(set(numbers)) == len(numbers), "product numbers are not unique"
    for pn in numbers:
        assert not any(c.islower() or c in "_:" for c in pn), pn
        assert mes_to_pn(mes_part_no(pn)) == pn and wms_to_pn(wms_item(pn)) == pn \
            and proc_to_pn(proc_part_ref(pn)) == pn, pn


# --- setup C: the four collections, built from the raw tables --------------------------------
COLLECTIONS = ("products", "work_orders", "sales_orders", "purchase_orders")
ID = {"products": "product_number", "work_orders": "work_order_id", "sales_orders": "sales_order_id",
      "purchase_orders": "purchase_order_id"}
# array fields and the key that orders their elements canonically
ARRAYS = {
    "products": {"standard_cost_history": ("start_date",), "list_price_history": ("start_date",),
                 "bom": ("component_number", "start_date"), "inventory": ("location.location_id",),
                 "vendors": ("vendor.vendor_id",)},
    "work_orders": {"routings": ("operation_sequence",)},
    "sales_orders": {"lines": ("line_id",)},
    "purchase_orders": {"lines": ("po_line_id",)},
}


def rows(df):
    return df.to_dict("records")


def _get(d, dotted):
    for p in dotted.split("."):
        d = d[p]
    return d


def canonical(coll, doc):
    """A document with every array in its canonical order (sources keep no order)."""
    doc = dict(doc)
    for f, key in ARRAYS[coll].items():
        doc[f] = sorted(doc[f], key=lambda x: tuple(_get(x, k) for k in key))
    return doc


def product_ref(p, sub, cat):
    """The product as other documents reference it: number, name, subcategory and category names."""
    s = sub.get(p["ProductSubcategoryID"])
    return {"product_number": p["ProductNumber"], "name": p["Name"],
            "subcategory": s["Name"] if s else None,
            "category": cat[s["ProductCategoryID"]]["Name"] if s else None}


def reference_state():
    """{collection: {id: document}} from the raw AdventureWorks tables, keyed by their integer ids."""
    T = {n: rows(aw.table(n)) for n in (
        "Product", "ProductSubcategory", "ProductCategory", "ProductModel", "BillOfMaterials", "WorkOrder",
        "WorkOrderRouting", "ScrapReason", "Location", "ProductInventory", "Vendor", "ProductVendor",
        "PurchaseOrderHeader", "PurchaseOrderDetail", "ShipMethod", "ProductCostHistory",
        "ProductListPriceHistory", "SalesOrderHeader", "SalesOrderDetail", "Customer", "Store", "SalesTerritory")}
    prod = {p["ProductID"]: p for p in T["Product"]}
    pn = {i: p["ProductNumber"] for i, p in prod.items()}
    sub = {s["ProductSubcategoryID"]: s for s in T["ProductSubcategory"]}
    cat = {c["ProductCategoryID"]: c for c in T["ProductCategory"]}
    model = {m["ProductModelID"]: m for m in T["ProductModel"]}
    loc = {l["LocationID"]: l for l in T["Location"]}
    vendor = {v["BusinessEntityID"]: v for v in T["Vendor"]}
    ref = {i: product_ref(p, sub, cat) for i, p in prod.items()}

    by = defaultdict(lambda: defaultdict(list))
    for r in T["ProductCostHistory"]:
        by["cost"][r["ProductID"]].append({"start_date": r["StartDate"], "end_date": r["EndDate"],
                                           "standard_cost": r["StandardCost"]})
    for r in T["ProductListPriceHistory"]:
        by["price"][r["ProductID"]].append({"start_date": r["StartDate"], "end_date": r["EndDate"],
                                            "list_price": r["ListPrice"]})
    for r in T["BillOfMaterials"]:
        if r["ProductAssemblyID"] is not None:   # level-0 rows name no assembly (SOURCE_SPLIT.md §3)
            by["bom"][r["ProductAssemblyID"]].append({
                "component_number": pn[r["ComponentID"]], "start_date": r["StartDate"], "end_date": r["EndDate"],
                "unit_measure_code": r["UnitMeasureCode"], "bom_level": r["BOMLevel"],
                "per_assembly_qty": r["PerAssemblyQty"]})
    for r in T["ProductInventory"]:
        by["inv"][r["ProductID"]].append({"location": {"location_id": r["LocationID"],
                                                       "name": loc[r["LocationID"]]["Name"]},
                                          "shelf": r["Shelf"], "bin": r["Bin"], "quantity": r["Quantity"]})
    for r in T["ProductVendor"]:
        v = vendor[r["BusinessEntityID"]]
        by["ven"][r["ProductID"]].append({
            "vendor": vendor_obj(v), "average_lead_time": r["AverageLeadTime"], "standard_price": r["StandardPrice"],
            "last_receipt_cost": r["LastReceiptCost"], "last_receipt_date": r["LastReceiptDate"],
            "min_order_qty": r["MinOrderQty"], "max_order_qty": r["MaxOrderQty"], "on_order_qty": r["OnOrderQty"],
            "unit_measure_code": r["UnitMeasureCode"]})

    products = {}
    for i, p in prod.items():
        s = sub.get(p["ProductSubcategoryID"])
        m = model.get(p["ProductModelID"])
        products[p["ProductNumber"]] = canonical("products", {
            **plm_product(p), "standard_cost": p["StandardCost"], "list_price": p["ListPrice"],
            "subcategory": None if s is None else {
                "subcategory_id": s["ProductSubcategoryID"], "name": s["Name"],
                "category": {"category_id": s["ProductCategoryID"], "name": cat[s["ProductCategoryID"]]["Name"]}},
            "model": None if m is None else {"model_id": m["ProductModelID"], "name": m["Name"]},
            "standard_cost_history": by["cost"][i], "list_price_history": by["price"][i], "bom": by["bom"][i],
            "inventory": by["inv"][i], "vendors": by["ven"][i]})

    routings = defaultdict(list)
    for r in T["WorkOrderRouting"]:
        routings[r["WorkOrderID"]].append({
            "operation_sequence": r["OperationSequence"],
            "work_center": {"work_center_id": r["LocationID"], "name": loc[r["LocationID"]]["Name"]},
            **{k: r[c] for k, c in ROUTING_FIELDS}})
    scrap = {s["ScrapReasonID"]: s for s in T["ScrapReason"]}
    work_orders = {}
    for w in T["WorkOrder"]:
        sr = scrap.get(w["ScrapReasonID"])
        work_orders[w["WorkOrderID"]] = canonical("work_orders", {
            "work_order_id": w["WorkOrderID"], "product": ref[w["ProductID"]],
            **{k: w[c] for k, c in WORK_ORDER_FIELDS},
            "scrap_reason": None if sr is None else {"scrap_reason_id": sr["ScrapReasonID"], "name": sr["Name"]},
            "routings": routings[w["WorkOrderID"]]})

    lines = defaultdict(list)
    for d in T["SalesOrderDetail"]:
        lines[d["SalesOrderID"]].append({"line_id": d["SalesOrderDetailID"], "product": ref[d["ProductID"]],
                                         **{k: d[c] for k, c in SALES_LINE_FIELDS}})
    store = {s["BusinessEntityID"]: s for s in T["Store"]}
    cust = {c["CustomerID"]: c for c in T["Customer"]}
    terr = {t["TerritoryID"]: t for t in T["SalesTerritory"]}
    sales_orders = {}
    for h in T["SalesOrderHeader"]:
        c = cust[h["CustomerID"]]
        st = store.get(c["StoreID"])
        t = terr[h["TerritoryID"]]
        sales_orders[h["SalesOrderID"]] = canonical("sales_orders", {
            "sales_order_id": h["SalesOrderID"], **{k: h[c_] for k, c_ in SALES_ORDER_FIELDS},
            "customer": {"customer_id": c["CustomerID"], "account_number": c["AccountNumber"],
                         "store": None if st is None else {"store_id": st["BusinessEntityID"], "name": st["Name"]}},
            "territory": {"territory_id": t["TerritoryID"], "name": t["Name"],
                          "country_region_code": t["CountryRegionCode"], "group": t["Group"]},
            "lines": lines[h["SalesOrderID"]]})

    po_lines = defaultdict(list)
    for d in T["PurchaseOrderDetail"]:
        po_lines[d["PurchaseOrderID"]].append({"po_line_id": d["PurchaseOrderDetailID"], "product": ref[d["ProductID"]],
                                               **{k: d[c] for k, c in PO_LINE_FIELDS}})
    ship = {s["ShipMethodID"]: s for s in T["ShipMethod"]}
    purchase_orders = {}
    for h in T["PurchaseOrderHeader"]:
        purchase_orders[h["PurchaseOrderID"]] = canonical("purchase_orders", {
            "purchase_order_id": h["PurchaseOrderID"], **{k: h[c] for k, c in PURCHASE_ORDER_FIELDS},
            "vendor": vendor_obj(vendor[h["VendorID"]]),
            "ship_method": {"ship_method_id": h["ShipMethodID"], "name": ship[h["ShipMethodID"]]["Name"]},
            "lines": po_lines[h["PurchaseOrderID"]]})
    return {"products": products, "work_orders": work_orders, "sales_orders": sales_orders,
            "purchase_orders": purchase_orders}


# (consolidated field, AdventureWorks column) per table; the split sources use the same names
PLM_PRODUCT_FIELDS = [("product_number", "ProductNumber"), ("name", "Name"), ("make_flag", "MakeFlag"),
                      ("finished_goods_flag", "FinishedGoodsFlag"), ("color", "Color"),
                      ("safety_stock_level", "SafetyStockLevel"), ("reorder_point", "ReorderPoint"),
                      ("size", "Size"), ("size_unit_measure_code", "SizeUnitMeasureCode"),
                      ("weight_unit_measure_code", "WeightUnitMeasureCode"), ("weight", "Weight"),
                      ("days_to_manufacture", "DaysToManufacture"), ("product_line", "ProductLine"),
                      ("class", "Class"), ("style", "Style"), ("sell_start_date", "SellStartDate"),
                      ("sell_end_date", "SellEndDate"), ("discontinued_date", "DiscontinuedDate")]
WORK_ORDER_FIELDS = [("order_qty", "OrderQty"), ("stocked_qty", "StockedQty"), ("scrapped_qty", "ScrappedQty"),
                     ("start_date", "StartDate"), ("end_date", "EndDate"), ("due_date", "DueDate")]
ROUTING_FIELDS = [("scheduled_start_date", "ScheduledStartDate"), ("scheduled_end_date", "ScheduledEndDate"),
                  ("actual_start_date", "ActualStartDate"), ("actual_end_date", "ActualEndDate"),
                  ("actual_resource_hrs", "ActualResourceHrs"), ("planned_cost", "PlannedCost"),
                  ("actual_cost", "ActualCost")]
SALES_ORDER_FIELDS = [("revision_number", "RevisionNumber"), ("order_date", "OrderDate"), ("due_date", "DueDate"),
                      ("ship_date", "ShipDate"), ("status", "Status"), ("online_order_flag", "OnlineOrderFlag"),
                      ("sales_order_number", "SalesOrderNumber"), ("purchase_order_number", "PurchaseOrderNumber"),
                      ("account_number", "AccountNumber"), ("sales_person_id", "SalesPersonID"),
                      ("ship_method_id", "ShipMethodID"), ("sub_total", "SubTotal"), ("tax_amt", "TaxAmt"),
                      ("freight", "Freight"), ("total_due", "TotalDue")]
SALES_LINE_FIELDS = [("carrier_tracking_number", "CarrierTrackingNumber"), ("order_qty", "OrderQty"),
                     ("special_offer_id", "SpecialOfferID"), ("unit_price", "UnitPrice"),
                     ("unit_price_discount", "UnitPriceDiscount"), ("line_total", "LineTotal")]
PURCHASE_ORDER_FIELDS = [("revision_number", "RevisionNumber"), ("status", "Status"), ("employee_id", "EmployeeID"),
                         ("order_date", "OrderDate"), ("ship_date", "ShipDate"), ("sub_total", "SubTotal"),
                         ("tax_amt", "TaxAmt"), ("freight", "Freight"), ("total_due", "TotalDue")]
PO_LINE_FIELDS = [("due_date", "DueDate"), ("order_qty", "OrderQty"), ("unit_price", "UnitPrice"),
                  ("line_total", "LineTotal"), ("received_qty", "ReceivedQty"), ("rejected_qty", "RejectedQty"),
                  ("stocked_qty", "StockedQty")]
VENDOR_FIELDS = [("vendor_id", "BusinessEntityID"), ("account_number", "AccountNumber"), ("name", "Name"),
                 ("credit_rating", "CreditRating"), ("preferred_vendor_status", "PreferredVendorStatus"),
                 ("active_flag", "ActiveFlag")]


def plm_product(p):
    return {k: p[c] for k, c in PLM_PRODUCT_FIELDS}


def vendor_obj(v):
    return {k: v[c] for k, c in VENDOR_FIELDS}


# --- comparing documents -----------------------------------------------------------------------
def diff(a, b, path=""):
    """First difference between two JSON-like values, or None. Numbers compare by value, floats to
    1e-9 relative (a NUMERIC that passed through a double), bools never equal numbers."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: only in {'first' if k in a else 'second'}"
            d = diff(a[k], b[k], f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items vs {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            d = diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return None
    num = (int, float)
    if isinstance(a, num) and isinstance(b, num) and not isinstance(a, bool) and not isinstance(b, bool):
        if a == b or math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9):
            return None
    elif type(a) is type(b) and a == b:
        return None
    return f"{path}: {a!r} vs {b!r}"


def verify(built, reference):
    """Every built document equals its reference; no document missing or extra. Returns problems."""
    problems = []
    for coll in COLLECTIONS:
        b, r = built.get(coll, {}), reference[coll]
        problems += [f"{coll}: missing {k}" for k in r if k not in b][:20]
        problems += [f"{coll}: extra {k}" for k in b if k not in r][:20]
        for k, doc in r.items():
            if k in b:
                d = diff(canonical(coll, b[k]), doc, f"{coll}[{k}]")
                if d:
                    problems.append(d)
    return problems


# --- dates -------------------------------------------------------------------------------------
# Every date field of setup C is named *date (start_date, order_date, sell_end_date, ...), and only those.
def is_date_field(name):
    return isinstance(name, str) and name.endswith("date")


def iso_dates(x):
    """Dates (datetime/date) anywhere in x as 'YYYY-MM-DD' text: how the reference states them."""
    if isinstance(x, dict):
        return {k: iso_dates(v) for k, v in x.items()}
    if isinstance(x, list):
        return [iso_dates(v) for v in x]
    if isinstance(x, datetime.datetime):
        assert x.time() == datetime.time(0), x   # SQL DATEs: midnight UTC
        return x.date().isoformat()
    if isinstance(x, datetime.date):
        return x.isoformat()
    return x


def bson_dates(x, key=None):
    """'YYYY-MM-DD' text in date fields as datetimes (midnight UTC), stored as BSON dates: the form
    Tapstate gives a source's SQL DATE in setup C."""
    if isinstance(x, dict):
        return {k: bson_dates(v, k) for k, v in x.items()}
    if isinstance(x, list):
        return [bson_dates(v, key) for v in x]
    if is_date_field(key) and isinstance(x, str):
        return datetime.datetime.fromisoformat(x)
    return x


def dumps(x):
    return json.dumps(x, sort_keys=True, default=str)
