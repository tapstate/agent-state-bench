"""The 20 manufacturing questions and their reference answers, computed from the raw AdventureWorks
tables only (aw.py), joined on AdventureWorks' own integer ids.

Answers never read the split sources or setup C, so a split or consolidation error cannot leak
into the ground truth. Every answer is checked here to be non-zero, and every "which one is
largest" question to have no tie.

Each question: id, category, the systems it needs (for the report), text, and an answer spec the
validator generator (make_queries.py) turns into validate.py:
  ("count", n)                   an integer that must appear as a standalone number
  ("money", x, tol)              a number within tol of x
  ("text", s)                    a string that must appear (case-insensitive)
  ("ids", [must])                every id in must appears
  ("labeled", [(label, spec)])   each spec is found within a window after its label
  ("all", [spec, ...])           every spec holds
"""
from collections import defaultdict

import aw

CONTROL, TWO, MULTI, PORTFOLIO = "single-source control", "two sources", "three or more sources", "portfolio"
TERRITORIES = ("Northwest", "Northeast", "Central", "Southwest", "Southeast", "Canada", "France", "Germany",
               "Australia", "United Kingdom")
CATEGORIES = ("Bikes", "Components", "Clothing", "Accessories")


def money(x):
    return round(float(x), 2)


def nonzero(x):
    assert x, "zero answer"
    return x


def unique_max(d):
    """(key, value) of the largest value; refuses a tie for first place."""
    top = sorted(d.items(), key=lambda kv: kv[1], reverse=True)
    assert len(top) == 1 or top[1][1] < top[0][1], f"tie for first place: {top[:3]}"
    return top[0]


class Data:
    """The raw tables, as lists of dicts, with the lookups the questions need."""

    def __init__(self):
        t = lambda n: aw.table(n).to_dict("records")  # noqa: E731
        self.prod = {p["ProductID"]: p for p in t("Product")}
        self.sub = {s["ProductSubcategoryID"]: s for s in t("ProductSubcategory")}
        self.cat = {c["ProductCategoryID"]: c["Name"] for c in t("ProductCategory")}
        self.model = {m["ProductModelID"]: m["Name"] for m in t("ProductModel")}
        self.loc = {l["LocationID"]: l["Name"] for l in t("Location")}
        self.vendor = {v["BusinessEntityID"]: v for v in t("Vendor")}
        self.terr = {x["TerritoryID"]: x["Name"] for x in t("SalesTerritory")}
        self.ship = {s["ShipMethodID"]: s["Name"] for s in t("ShipMethod")}
        self.wo = t("WorkOrder")
        self.inv = t("ProductInventory")
        self.pv = t("ProductVendor")
        self.poh = {h["PurchaseOrderID"]: h for h in t("PurchaseOrderHeader")}
        self.pod = t("PurchaseOrderDetail")
        self.soh = {h["SalesOrderID"]: h for h in t("SalesOrderHeader")}
        self.sod = t("SalesOrderDetail")
        self.cost_hist = defaultdict(list)
        for r in t("ProductCostHistory"):
            self.cost_hist[r["ProductID"]].append(r)
        self.onhand = defaultdict(int)
        for r in self.inv:
            self.onhand[r["ProductID"]] += r["Quantity"]

    def subname(self, pid):
        s = self.sub.get(self.prod[pid]["ProductSubcategoryID"])
        return s["Name"] if s else None

    def catname(self, pid):
        s = self.sub.get(self.prod[pid]["ProductSubcategoryID"])
        return self.cat[s["ProductCategoryID"]] if s else None

    def modelname(self, pid):
        return self.model.get(self.prod[pid]["ProductModelID"])

    def name(self, pid):
        return self.prod[pid]["Name"]

    def number(self, pid):
        return self.prod[pid]["ProductNumber"]

    def std_cost(self, pid):           # current standard cost (Product.StandardCost)
        return self.prod[pid]["StandardCost"]

    def cost_on(self, pid, day):
        """The standard cost in effect on a day: the history period that contains it."""
        hit = [r["StandardCost"] for r in self.cost_hist[pid]
               if r["StartDate"] <= day and (r["EndDate"] is None or day <= r["EndDate"])]
        assert len(hit) == 1, (pid, day, hit)
        return hit[0]

    def sales_lines(self, start, end):
        """(header, detail) for orders dated start..end inclusive."""
        for d in self.sod:
            h = self.soh[d["SalesOrderID"]]
            if start <= h["OrderDate"] <= end:
                yield h, d

    def work_orders(self, start, end):
        return [w for w in self.wo if start <= w["StartDate"] <= end]


def build(data=None):
    x = data or Data()
    Q = []

    def q(cat, systems, text, spec):
        Q.append({"id": len(Q) + 1, "category": cat, "systems": systems, "text": text, "answer": spec})

    y24 = ("2024-01-01", "2024-12-31")

    # 1-4: single-source controls ---------------------------------------------------------------
    n = sum(1 for w in x.work_orders(*y24) if w["ScrappedQty"] > 0)
    q(CONTROL, ["mes"], "How many work orders that started in 2024 scrapped at least one unit?", ("count", nonzero(n)))

    by = defaultdict(float)
    for h in x.soh.values():
        if y24[0] <= h["OrderDate"] <= y24[1]:
            by[x.terr[h["TerritoryID"]]] += h["SubTotal"]
    top, v = unique_max(by)
    q(CONTROL, ["erp"],
      "Which sales territory had the highest total order subtotal (before tax and freight) on sales orders placed "
      "in 2024, and what was that total? Give the amount in US dollars to two decimals.",
      ("all", [("text", top), ("money", money(v), 1.0)]))

    by = defaultdict(int)
    for r in x.inv:
        by[x.loc[r["LocationID"]]] += r["Quantity"]
    top, v = unique_max(by)
    q(CONTROL, ["wms"], "Which location holds the largest total quantity of stock, and how many units is that?",
      ("all", [("text", top), ("count", v)]))

    v = sum(h["Freight"] for h in x.poh.values()
            if y24[0] <= h["OrderDate"] <= y24[1] and x.ship[h["ShipMethodID"]] == "OVERSEAS - DELUXE")
    q(CONTROL, ["procurement"],
      "What is the total freight on purchase orders placed in 2024 that use the ship method 'OVERSEAS - DELUXE'? "
      "Give the amount in US dollars to two decimals.", ("money", money(nonzero(v)), 1.0))

    # 5-10: two sources -------------------------------------------------------------------------
    n = sum(w["ScrappedQty"] for w in x.work_orders(*y24) if x.subname(w["ProductID"]) == "Wheels")
    q(TWO, ["mes", "plm"],
      "How many units in total were scrapped by work orders that started in 2024 for products in the 'Wheels' "
      "subcategory?", ("count", nonzero(n)))

    n = sum(d["OrderQty"] for _, d in x.sales_lines(*y24) if x.modelname(d["ProductID"]) == "Mountain-200")
    q(TWO, ["erp", "plm"],
      "How many units of products of the 'Mountain-200' model were sold on sales orders placed in 2024?",
      ("count", nonzero(n)))

    n = sum(r["Quantity"] for r in x.inv if x.prod[r["ProductID"]]["Color"] == "Silver"
            and x.loc[r["LocationID"]] == "Finished Goods Storage")
    q(TWO, ["wms", "plm"], "How many units of Silver-colored products are in stock at the 'Finished Goods Storage' "
                           "location?", ("count", nonzero(n)))

    lead = [r["AverageLeadTime"] for r in x.pv if x.subname(r["ProductID"]) == "Tires and Tubes"]
    q(TWO, ["procurement", "plm"],
      "Across all vendor offers (one per vendor and product) for products in the 'Tires and Tubes' subcategory, what "
      "is the mean average lead time in days? Give it to two decimals.",
      ("money", money(nonzero(sum(lead) / len(lead))), 0.01))

    v = sum(w["ScrappedQty"] * x.std_cost(w["ProductID"]) for w in x.work_orders("2024-10-01", "2024-12-31"))
    q(TWO, ["mes", "costing"],
      "What is the value of all units scrapped by work orders that started in the fourth quarter of 2024 "
      "(October to December), valued at each product's current standard cost? Give the amount in US dollars to "
      "two decimals.", ("money", money(nonzero(v)), 1.0))

    vid = next(i for i, v_ in x.vendor.items() if v_["Name"] == "Bergeron Off-Roads")
    n = sum(x.onhand[r["ProductID"]] for r in x.pv if r["BusinessEntityID"] == vid)
    q(TWO, ["procurement", "wms"],
      "What is the total quantity on hand, across all locations, of the items that the vendor 'Bergeron Off-Roads' "
      "supplies?", ("count", nonzero(n)))

    # 11-16: three or more sources ----------------------------------------------------------------
    by = defaultdict(int)
    for w in x.work_orders(*y24):
        if x.subname(w["ProductID"]) == "Wheels":
            by[w["ProductID"]] += w["ScrappedQty"]
    pid, _ = unique_max(by)
    q(MULTI, ["mes", "plm", "wms"],
      "Which product in the 'Wheels' subcategory had the most units scrapped by work orders that started in 2024, "
      "and how many units of that product are currently on hand across all locations?",
      ("all", [("text", x.name(pid)), ("count", nonzero(x.onhand[pid]))]))

    lines = [d for _, d in x.sales_lines("2025-01-01", "2025-03-31") if x.modelname(d["ProductID"]) == "Mountain-200"]
    v = sum(d["LineTotal"] - d["OrderQty"] * x.std_cost(d["ProductID"]) for d in lines)
    q(MULTI, ["erp", "plm", "costing"],
      "What was the gross margin on sales of 'Mountain-200' model products on sales orders placed in the first "
      "quarter of 2025 (January to March), computed as the sum of line totals minus quantity times each product's "
      "current standard cost? Give the amount in US dollars to two decimals.", ("money", money(nonzero(v)), 1.0))

    sold = sum(d["OrderQty"] for _, d in x.sales_lines(*y24) if x.catname(d["ProductID"]) == "Clothing")
    recv = sum(d["ReceivedQty"] for d in x.pod if x.catname(d["ProductID"]) == "Clothing"
               and y24[0] <= x.poh[d["PurchaseOrderID"]]["OrderDate"] <= y24[1])
    q(MULTI, ["erp", "procurement", "plm"],
      "For products in the Clothing category: how many units were sold on sales orders placed in 2024, and how many "
      "units were received on purchase orders placed in 2024? Answer in the form 'sold: N, received: N'.",
      ("labeled", [("sold", ("count", nonzero(sold))), ("received", ("count", int(nonzero(recv))))]))

    below = sorted((pid for pid, p in x.prod.items() if p["MakeFlag"] == 0 and x.catname(pid) == "Components"
                    and x.onhand[pid] < p["SafetyStockLevel"]), key=x.number)
    shortest = {pid: min(r["AverageLeadTime"] for r in x.pv if r["ProductID"] == pid) for pid in below}
    assert below and all(shortest.values())
    q(MULTI, ["plm", "wms", "procurement"],
      "Which purchased products (make_flag 0) in the Components category have a total quantity on hand, across all "
      "locations, below their safety stock level? For each, give its product number and the shortest average lead "
      "time, in days, among the vendors that supply it.",
      ("all", [("ids", [x.number(p) for p in below])]
       + [("labeled", [([x.number(p), x.name(p)], ("count", shortest[p]))]) for p in below]))

    by = defaultdict(int)
    for _, d in x.sales_lines("2024-10-01", "2024-12-31"):
        if x.catname(d["ProductID"]) == "Bikes":
            by[d["ProductID"]] += d["OrderQty"]
    pid, _ = unique_max(by)
    q(MULTI, ["erp", "plm", "wms"],
      "Which product in the Bikes category sold the most units on sales orders placed in the fourth quarter of "
      "2024 (October to December), and how many units of it are currently on hand across all locations?",
      ("all", [("text", x.name(pid)), ("count", nonzero(x.onhand[pid]))]))

    sold = sum(d["OrderQty"] for _, d in x.sales_lines(*y24) if x.subname(d["ProductID"]) == "Road Bikes")
    stocked = sum(w["StockedQty"] for w in x.wo if y24[0] <= w["EndDate"] <= y24[1]
                  and x.subname(w["ProductID"]) == "Road Bikes")
    q(MULTI, ["erp", "mes", "plm"],
      "For products in the 'Road Bikes' subcategory: how many units were sold on sales orders placed in 2024, and how "
      "many units were stocked by work orders that ended in 2024? Answer in the form 'sold: N, stocked: N'.",
      ("labeled", [("sold", ("count", nonzero(sold))), ("stocked", ("count", nonzero(stocked)))]))

    # 17-20: portfolio aggregates ----------------------------------------------------------------
    by = defaultdict(int)
    for w in x.work_orders(*y24):
        by[x.catname(w["ProductID"])] += w["ScrappedQty"]
    q(PORTFOLIO, ["mes", "plm"],
      "For each of the product categories Bikes and Components, how many units were scrapped by work orders that "
      "started in 2024?",
      ("labeled", [(c, ("count", nonzero(by[c]))) for c in ("Bikes", "Components")]))

    by = defaultdict(float)
    for h, d in x.sales_lines(*y24):
        if x.catname(d["ProductID"]) == "Bikes":
            by[x.terr[h["TerritoryID"]]] += d["LineTotal"]
    q(PORTFOLIO, ["erp", "plm"],
      "For each sales territory, what was the revenue (sum of sales order line totals) from products in the Bikes "
      "category on sales orders placed in 2024? Give amounts in US dollars to two decimals.",
      ("labeled", [(t, ("money", money(nonzero(by[t])), 1.0)) for t in TERRITORIES]))

    by = defaultdict(float)
    for r in x.inv:
        by[x.catname(r["ProductID"])] += r["Quantity"] * x.std_cost(r["ProductID"])
    q(PORTFOLIO, ["wms", "plm", "costing"],
      "What is the value of the stock on hand, at each product's current standard cost, for each product category "
      "(Bikes, Components, Clothing, Accessories)? Give amounts in US dollars to two decimals.",
      ("labeled", [(c, ("money", money(nonzero(by[c])), 1.0)) for c in CATEGORIES]))

    v = sum(d["LineTotal"] - d["OrderQty"] * x.cost_on(d["ProductID"], h["OrderDate"])
            for h, d in x.sales_lines("2023-01-01", "2023-12-31") if x.catname(d["ProductID"]) == "Bikes")
    q(PORTFOLIO, ["erp", "plm", "costing"],
      "What was the gross margin on Bikes-category products sold on sales orders placed in 2023, computed as the sum "
      "of line totals minus quantity times the product's standard cost in effect on the order date (from the "
      "standard cost history)? Give the amount in US dollars to two decimals.", ("money", money(nonzero(v)), 1.0))
    return Q


if __name__ == "__main__":
    import json
    for qq in build():
        print(qq["id"], qq["category"], json.dumps(qq["answer"]))
