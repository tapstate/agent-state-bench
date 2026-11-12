"""Read Microsoft's AdventureWorks OLTP sample (the install script's CSV files) into pandas.

Column names come from the CREATE TABLE statements in the script's own instawdb.sql, and field
and row terminators from its BULK INSERT statements, so nothing about the files is guessed.
Values are read as text and typed per table in TYPES; nothing else is changed.
"""
import re
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
RAW = HERE / "data" / "raw"


def _schema(sql):
    """table -> (columns, field terminator, row terminator) from instawdb.sql."""
    cols = {}
    for m in re.finditer(r"^CREATE TABLE \[\w+\]\.\[(\w+)\]\(\n(.*?)^\)", sql, re.M | re.S):
        cols[m.group(1)] = [c.group(1) for c in re.finditer(r"^\s+\[(\w+)\]", m.group(2), re.M)]
    out = {}
    for m in re.finditer(r"BULK INSERT \[\w+\]\.\[(\w+)\] FROM '\$\(SqlSamplesSourceDataPath\)(\w+)\.csv'\s*WITH \((.*?)\);",
                         sql, re.S):
        opts = m.group(3)
        ft = re.search(r"FIELDTERMINATOR\s*=\s*'([^']*)'", opts).group(1)
        rt = re.search(r"ROWTERMINATOR\s*=\s*'([^']*)'", opts).group(1)
        out[m.group(1)] = (cols[m.group(1)], "\t" if ft == "\\t" else ft, "\n" if rt in ("0x0a", "\\n") else
                           rt.replace("\\n", "\n"))
    return out


_SCHEMA = None


def schema():
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = _schema((RAW / "instawdb.sql").read_text(encoding="utf-8-sig", errors="replace"))
    return _SCHEMA


INT, NUM, DATE, TEXT = "int", "num", "date", "text"
# Only the tables the benchmark uses; columns not listed stay text.
TYPES = {
    "Product": {"ProductID": INT, "MakeFlag": INT, "FinishedGoodsFlag": INT, "SafetyStockLevel": INT,
                "ReorderPoint": INT, "StandardCost": NUM, "ListPrice": NUM, "Weight": NUM,
                "DaysToManufacture": INT, "ProductSubcategoryID": INT, "ProductModelID": INT,
                "SellStartDate": DATE, "SellEndDate": DATE, "DiscontinuedDate": DATE},
    "ProductSubcategory": {"ProductSubcategoryID": INT, "ProductCategoryID": INT},
    "ProductCategory": {"ProductCategoryID": INT},
    "ProductModel": {"ProductModelID": INT},
    "BillOfMaterials": {"BillOfMaterialsID": INT, "ProductAssemblyID": INT, "ComponentID": INT, "StartDate": DATE,
                        "EndDate": DATE, "BOMLevel": INT, "PerAssemblyQty": NUM},
    "WorkOrder": {"WorkOrderID": INT, "ProductID": INT, "OrderQty": INT, "StockedQty": INT, "ScrappedQty": INT,
                  "StartDate": DATE, "EndDate": DATE, "DueDate": DATE, "ScrapReasonID": INT},
    "WorkOrderRouting": {"WorkOrderID": INT, "ProductID": INT, "OperationSequence": INT, "LocationID": INT,
                         "ScheduledStartDate": DATE, "ScheduledEndDate": DATE, "ActualStartDate": DATE,
                         "ActualEndDate": DATE, "ActualResourceHrs": NUM, "PlannedCost": NUM, "ActualCost": NUM},
    "ScrapReason": {"ScrapReasonID": INT},
    "Location": {"LocationID": INT, "CostRate": NUM, "Availability": NUM},
    "ProductInventory": {"ProductID": INT, "LocationID": INT, "Bin": INT, "Quantity": INT},
    "TransactionHistory": {"TransactionID": INT, "ProductID": INT, "ReferenceOrderID": INT,
                           "ReferenceOrderLineID": INT, "TransactionDate": DATE, "Quantity": INT, "ActualCost": NUM},
    "Vendor": {"BusinessEntityID": INT, "CreditRating": INT, "PreferredVendorStatus": INT, "ActiveFlag": INT},
    "ProductVendor": {"ProductID": INT, "BusinessEntityID": INT, "AverageLeadTime": INT, "StandardPrice": NUM,
                      "LastReceiptCost": NUM, "LastReceiptDate": DATE, "MinOrderQty": INT, "MaxOrderQty": INT,
                      "OnOrderQty": INT},
    "PurchaseOrderHeader": {"PurchaseOrderID": INT, "RevisionNumber": INT, "Status": INT, "EmployeeID": INT,
                            "VendorID": INT, "ShipMethodID": INT, "OrderDate": DATE, "ShipDate": DATE,
                            "SubTotal": NUM, "TaxAmt": NUM, "Freight": NUM, "TotalDue": NUM},
    "PurchaseOrderDetail": {"PurchaseOrderID": INT, "PurchaseOrderDetailID": INT, "DueDate": DATE, "OrderQty": INT,
                            "ProductID": INT, "UnitPrice": NUM, "LineTotal": NUM, "ReceivedQty": NUM,
                            "RejectedQty": NUM, "StockedQty": NUM},
    "ShipMethod": {"ShipMethodID": INT, "ShipBase": NUM, "ShipRate": NUM},
    "ProductCostHistory": {"ProductID": INT, "StartDate": DATE, "EndDate": DATE, "StandardCost": NUM},
    "ProductListPriceHistory": {"ProductID": INT, "StartDate": DATE, "EndDate": DATE, "ListPrice": NUM},
    "SalesOrderHeader": {"SalesOrderID": INT, "RevisionNumber": INT, "OrderDate": DATE, "DueDate": DATE,
                         "ShipDate": DATE, "Status": INT, "OnlineOrderFlag": INT, "CustomerID": INT,
                         "SalesPersonID": INT, "TerritoryID": INT, "ShipMethodID": INT, "SubTotal": NUM,
                         "TaxAmt": NUM, "Freight": NUM, "TotalDue": NUM},
    "SalesOrderDetail": {"SalesOrderID": INT, "SalesOrderDetailID": INT, "OrderQty": INT, "ProductID": INT,
                         "SpecialOfferID": INT, "UnitPrice": NUM, "UnitPriceDiscount": NUM, "LineTotal": NUM},
    "Customer": {"CustomerID": INT, "PersonID": INT, "StoreID": INT, "TerritoryID": INT},
    "Store": {"BusinessEntityID": INT, "SalesPersonID": INT},
    "SalesTerritory": {"TerritoryID": INT},
    "Person": {"BusinessEntityID": INT},
}


def table(name):
    """One AdventureWorks table as a DataFrame: typed columns, None for empty fields."""
    cols, ft, rt = schema()[name]
    raw = (RAW / f"{name}.csv").read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    rows = [r for r in text.split(rt) if r.strip("\r\n")]
    data = [r.rstrip("\r").split(ft) for r in rows]
    bad = [r for r in data if len(r) != len(cols)]
    if bad:
        raise ValueError(f"{name}: {len(bad)} rows do not have {len(cols)} fields, e.g. {bad[0][:5]}")
    df = pd.DataFrame(data, columns=cols).replace({"": None})
    for c, t in TYPES.get(name, {}).items():
        if t == INT:
            df[c] = pd.array([None if v is None else int(v) for v in df[c]], dtype="Int64")
        elif t == NUM:
            df[c] = [None if v is None else float(v) for v in df[c]]
        elif t == DATE:   # AdventureWorks datetimes are all midnight; keep the day
            df[c] = [None if v is None else v[:10] for v in df[c]]
    return df.astype(object).where(df.notna(), None)
