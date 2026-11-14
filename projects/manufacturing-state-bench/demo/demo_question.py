"""The sales-agent demo question: one customer question whose answer needs all six source systems.

A store asks to reorder the bike it buys most. Answering takes:
  ERP          the store's orders since 1 July 2024, and the bike it bought the most units of
  PLM          the bike's name, its bill of materials, and which components are bought in (make_flag 0)
  Costing      the bike's current list price
  WMS          units of the bike on hand, across all locations
  MES          units of the bike stocked by work orders that ended in Q2 2025
  Procurement  the longest vendor lead time among the bought-in components

The answer is computed from the raw AdventureWorks tables only (as in dab/questions.py), never from
the split sources or from Tapstate's state, so both setups are checked against the same reference.

Writes query<DEMO_ID>/ (query.json, ground_truth.csv, validate.py) into query_manufacturing and
query_manufacturing_consolidated, the same files dab/make_queries.py writes for the 20 benchmark
questions. Run it after make_queries.py, which removes query directories it does not own.

Usage: python demo_question.py <dab-root>        # writes the query directories
       python demo_question.py --print           # prints the question and its answer only
"""
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "dab"))

import aw  # noqa: E402
from answer_check import describe  # noqa: E402
from common import CONSOLIDATED, DATASET  # noqa: E402
from make_queries import VALIDATE, gt_lines  # noqa: E402
from questions import Data, unique_max  # noqa: E402

DEMO_ID = 101          # outside the benchmark's 1-20, so the demo never mixes into its results
STORE = "Rural Cycle Emporium"
SINCE = "2024-07-01"
BUILT = ("2025-04-01", "2025-06-30")

TEXT = (
    f"A customer writes in: \"Hi, this is {STORE}. We'd like to reorder the bike we have bought the most units "
    f"of from you on orders placed since 1 July 2024. Which bike is that, and what is its current list price? "
    f"How many units can you ship from stock today, across all your locations? How many units of it did your "
    f"factory finish in the second quarter of 2025 (work orders that ended April to June 2025)? And if you have "
    f"to build more, what is the longest supplier lead time, in days, among the parts you buy in for it?\" "
    f"Answer in the form 'bike: NAME, list price: $X, in stock: N, built in Q2 2025: N, longest lead time: N days'."
)


def answer(x=None):
    x = x or Data()
    store_id = next(int(s["BusinessEntityID"]) for s in aw.table("Store").to_dict("records") if s["Name"] == STORE)
    customers = {c["CustomerID"] for c in aw.table("Customer").to_dict("records")
                 if c["StoreID"] is not None and str(c["StoreID"]) != "nan" and int(float(c["StoreID"])) == store_id}
    units = Counter()
    for d in x.sod:
        h = x.soh[d["SalesOrderID"]]
        if h["CustomerID"] in customers and h["OrderDate"] >= SINCE and x.catname(d["ProductID"]) == "Bikes":
            units[d["ProductID"]] += d["OrderQty"]
    pid, _ = unique_max(units)

    on_hand = x.onhand[pid]
    built = sum(w["StockedQty"] for w in x.wo if w["ProductID"] == pid and BUILT[0] <= w["EndDate"] <= BUILT[1])
    bom = [b for b in aw.table("BillOfMaterials").to_dict("records")
           if b["ProductAssemblyID"] == pid and b["EndDate"] is None]
    bought = {b["ComponentID"] for b in bom if x.prod[b["ComponentID"]]["MakeFlag"] == 0}
    lead = max(r["AverageLeadTime"] for r in x.pv if r["ProductID"] in bought)
    assert on_hand and built and bought
    return {
        "product_number": x.number(pid), "name": x.name(pid), "list_price": x.prod[pid]["ListPrice"],
        "on_hand": on_hand, "built_q2_2025": built, "longest_lead_days": lead,
        "bought_in": sorted(x.number(c) for c in bought),
    }


def spec(a):
    return ["labeled", [["bike", ["text", a["name"]]], ["list price", ["money", round(a["list_price"], 2), 0.01]],
                        ["in stock", ["count", a["on_hand"]]], ["built in Q2 2025", ["count", a["built_q2_2025"]]],
                        ["longest lead time", ["count", a["longest_lead_days"]]]]]


def main():
    a = answer()
    s = spec(a)
    if "--print" in sys.argv:
        print(TEXT, "\n")
        print(json.dumps(a, indent=1))
        print("expected:", describe(s))
        return
    dab = Path(sys.argv[1]).resolve()
    checker = (HERE.parent / "dab" / "answer_check.py").read_text().split('"""', 2)[2].strip()
    for ds in (DATASET, CONSOLIDATED):
        d = dab / f"query_{ds}" / f"query{DEMO_ID}"
        d.mkdir(parents=True, exist_ok=True)
        (d / "query.json").write_text(json.dumps(TEXT, ensure_ascii=False))
        (d / "ground_truth.csv").write_text("\n".join(gt_lines(s)) + "\n")
        (d / "validate.py").write_text(VALIDATE.format(id=DEMO_ID, category="sales demo, six systems",
                                                       spec=repr(s), checker=checker))
    print(f"wrote query{DEMO_ID} to query_{DATASET} and query_{CONSOLIDATED}: {describe(s)}")


if __name__ == "__main__":
    main()
