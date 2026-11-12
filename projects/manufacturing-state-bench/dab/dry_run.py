"""Dry run of the manufacturing datasets through DAB's own tools, with no model calls.

For setup A (query_manufacturing) and setup C (query_manufacturing_consolidated):
  1. DAB's loader (the runners' warm step) finds every server-side database in place.
  2. list_db on every database, through DAB's ListDBTool.
  3. Six reference questions answered with real queries through DAB's QueryDBTool: in A by
     joining the systems on their own key formats, in C with MongoDB aggregations. Each answer is
     scored by that question's own validate.py.
  4. One execute_python call in DAB's sandbox container.
Then builds both setups' prompts exactly as run_claude_arms.py does.

Run with the docker group active (the sandbox is a container), from this directory:
  python dry_run.py <dab-root> <bench-harness-dir>
"""
import importlib.util
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

DAB = Path(sys.argv[1]).resolve()
HARNESS = Path(sys.argv[2]).resolve()
for k, v in {"DAB_PERSISTENT_DBS": "1", "PG_HOST": "127.0.0.1", "PG_PORT": "55432", "PG_USER": "postgres",
             "PG_PASSWORD": "secret", "MONGO_URI": "mongodb://127.0.0.1:27017/?directConnection=true"}.items():
    os.environ.setdefault(k, v)  # defaults; the environment overrides them
os.environ["PATH"] = f"{HARNESS / 'bin'}:{os.environ['PATH']}"
os.chdir(DAB)
sys.path[:0] = [str(DAB), str(HARNESS)]

from common_scaffold.tools.ExecTool import ExecTool  # noqa: E402
from common_scaffold.tools.ListDBTool import ListDBTool  # noqa: E402
from common_scaffold.tools.QueryDBTool import QueryDBTool  # noqa: E402
from run_arms import warm  # noqa: E402
from run_claude_arms import build_prompts  # noqa: E402

LOG = Path("/tmp") / "mfg_dry_run_tools.jsonl"
failures = []


def check(cond, what):
    print(("  ok    " if cond else "  FAIL  ") + what)
    if not cond:
        failures.append(what)


def tools(ds):
    cfg = DAB / f"query_{ds}" / "db_config.yaml"
    return (QueryDBTool(log_path=LOG, name="query_db", db_config_path=cfg, check_load=True),
            ListDBTool(log_path=LOG, name="list_db", db_config_path=cfg, check_load=False))


def run(query_tool, db, query):
    out = query_tool.exec({"db_name": db, "query": query if isinstance(query, str) else json.dumps(query)})
    if not out["success"]:
        raise RuntimeError(f"{db}: {out['result']}")
    return out["result"]


def score(ds, q, answer):
    spec = importlib.util.spec_from_file_location("v", DAB / f"query_{ds}" / f"query{q}" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, reason = mod.validate(answer)
    check(ok, f"q{q} answered through query_db: {answer[:120]!r} ({reason[:100]})")


def one(rows):
    return list(rows[0].values())[0]


def setup_a():
    ds = "manufacturing"
    print(f"== query_{ds} (setup A)")
    warm(DAB, [ds])
    check(True, "DAB loader found mfg_erp, mfg_costing and mfg_wms in place")
    q, ls = tools(ds)
    for db in ("erp", "plm", "mes", "wms", "procurement", "costing"):
        out = ls.exec({"db_name": db})
        check(out["success"] and len(out["result"]) > 0, f"list_db {db}: {out['result']}")

    score(ds, 1, str(one(run(q, "mes", "SELECT count(*) FROM work_orders WHERE start_date BETWEEN "
                                       "'2024-01-01' AND '2024-12-31' AND scrapped_qty > 0"))))
    # q3: WMS stock per location (MongoDB aggregation, locations by _id)
    top = run(q, "wms", {"collection": "stock", "pipeline": [
        {"$group": {"_id": "$location_id", "q": {"$sum": "$quantity"}}}, {"$sort": {"q": -1}}, {"$limit": 1}]})[0]
    name = run(q, "wms", {"collection": "locations", "filter": {"_id": int(top["_id"])}})[0]["name"]
    score(ds, 3, f"{name}: {top['q']} units")
    # q5: scrap (MES, part_no lower case) for the Wheels subcategory (PLM, canonical numbers)
    wheels = {r["product_number"].lower() for r in run(q, "plm", "SELECT p.product_number FROM products p JOIN "
                                                                 "subcategories s USING (subcategory_id) "
                                                                 "WHERE s.name = 'Wheels'")}
    rows = run(q, "mes", "SELECT part_no, sum(scrapped_qty) AS s FROM work_orders WHERE start_date BETWEEN "
                         "'2024-01-01' AND '2024-12-31' GROUP BY part_no")
    score(ds, 5, str(sum(int(float(r["s"])) for r in rows if r["part_no"] in wheels)))
    # q10: on hand (WMS, 'AW:' items) of what a vendor supplies (procurement, '_' part_ref)
    refs = {r["part_ref"] for r in run(q, "procurement", "SELECT vi.part_ref FROM vendor_items vi JOIN vendors v "
                                                         "USING (vendor_id) WHERE v.name = 'Bergeron Off-Roads'")}
    items = ["AW:" + r.replace("_", "-") for r in refs]
    rows = run(q, "wms", {"collection": "stock", "pipeline": [{"$match": {"item": {"$in": items}}},
                                                              {"$group": {"_id": None, "q": {"$sum": "$quantity"}}}]})
    score(ds, 10, str(rows[0]["q"]))
    # q13: clothing sold (ERP sku) and received (procurement part_ref) in 2024
    clothing = {r["product_number"] for r in run(q, "plm", "SELECT p.product_number FROM products p JOIN "
                                                           "subcategories s USING (subcategory_id) JOIN categories c "
                                                           "USING (category_id) WHERE c.name = 'Clothing'")}
    sold = run(q, "erp", "SELECT l.sku, sum(l.order_qty) AS n FROM sales_order_lines l JOIN sales_orders o "
                         "USING (sales_order_id) WHERE o.order_date BETWEEN '2024-01-01' AND '2024-12-31' "
                         "GROUP BY l.sku")
    recv = run(q, "procurement", "SELECT l.part_ref, sum(l.received_qty) AS n FROM po_lines l JOIN purchase_orders o "
                                 "USING (purchase_order_id) WHERE o.order_date BETWEEN '2024-01-01' AND '2024-12-31' "
                                 "GROUP BY l.part_ref")
    s = sum(int(float(r["n"])) for r in sold if r["sku"] in clothing)
    rc = sum(float(r["n"]) for r in recv if r["part_ref"].replace("_", "-") in clothing)
    score(ds, 13, f"sold: {s}, received: {rc:.0f}")
    # q19: stock value at current standard cost by category (WMS + PLM + costing)
    cat = {r["product_number"]: r["c"] for r in run(q, "plm", "SELECT p.product_number, c.name AS c FROM products p "
                                                              "JOIN subcategories s USING (subcategory_id) JOIN "
                                                              "categories c USING (category_id)")}
    cost = {r["product_number"]: float(r["standard_cost"])
            for r in run(q, "costing", "SELECT product_number, standard_cost FROM item_prices")}
    by = defaultdict(float)
    for r in run(q, "wms", {"collection": "stock", "projection": {"item": 1, "quantity": 1, "_id": 0}}):
        pn = r["item"].split(":", 1)[1]
        if pn in cat:
            by[cat[pn]] += int(float(r["quantity"])) * cost[pn]
    score(ds, 19, "\n".join(f"{c}: {v:.2f}" for c, v in by.items()))


def setup_c():
    ds = "manufacturing_consolidated"
    print(f"== query_{ds} (setup C)")
    warm(DAB, [ds])
    check(True, "DAB loader found mfg_state in place")
    q, ls = tools(ds)
    out = ls.exec({"db_name": "mfg_state"})
    check(out["success"] and all(c in json.dumps(out["result"]) for c in
                                 ("products", "work_orders", "sales_orders", "purchase_orders")),
          f"list_db mfg_state: {out['result']}")

    def agg(coll, pipeline):
        return run(q, "mfg_state", {"collection": coll, "pipeline": pipeline})

    y24 = {"$gte": "2024-01-01", "$lte": "2024-12-31"}
    score(ds, 1, str(one(agg("work_orders", [{"$match": {"start_date": y24, "scrapped_qty": {"$gt": 0}}},
                                             {"$count": "n"}]))))
    r = agg("products", [{"$unwind": "$inventory"}, {"$group": {"_id": "$inventory.location.name",
                                                                "q": {"$sum": "$inventory.quantity"}}},
                         {"$sort": {"q": -1}}, {"$limit": 1}])[0]
    score(ds, 3, f"{r['_id']}: {r['q']} units")
    score(ds, 5, str(one(agg("work_orders", [{"$match": {"start_date": y24, "product.subcategory": "Wheels"}},
                                             {"$group": {"_id": None, "s": {"$sum": "$scrapped_qty"}}},
                                             {"$project": {"_id": 0}}]))))
    score(ds, 10, str(one(agg("products", [{"$match": {"vendors.vendor.name": "Bergeron Off-Roads"}},
                                           {"$unwind": "$inventory"},
                                           {"$group": {"_id": None, "q": {"$sum": "$inventory.quantity"}}},
                                           {"$project": {"_id": 0}}]))))
    s = one(agg("sales_orders", [{"$match": {"order_date": y24}}, {"$unwind": "$lines"},
                                 {"$match": {"lines.product.category": "Clothing"}},
                                 {"$group": {"_id": None, "n": {"$sum": "$lines.order_qty"}}},
                                 {"$project": {"_id": 0}}]))
    rc = one(agg("purchase_orders", [{"$match": {"order_date": y24}}, {"$unwind": "$lines"},
                                     {"$match": {"lines.product.category": "Clothing"}},
                                     {"$group": {"_id": None, "n": {"$sum": "$lines.received_qty"}}},
                                     {"$project": {"_id": 0}}]))
    score(ds, 13, f"sold: {s}, received: {float(rc):.0f}")
    rows = agg("products", [{"$match": {"subcategory": {"$ne": None}}}, {"$unwind": "$inventory"},
                            {"$group": {"_id": "$subcategory.category.name",
                                        "v": {"$sum": {"$multiply": ["$inventory.quantity", "$standard_cost"]}}}}])
    score(ds, 19, "\n".join(f"{r['_id']}: {float(r['v']):.2f}" for r in rows))


def sandbox():
    print("== execute_python sandbox")
    work = Path("/tmp/mfg_dry_run_exec")
    work.mkdir(exist_ok=True)
    t = ExecTool(log_path=LOG, name="execute_python", work_dir=work, timeout=120)
    try:
        out = t.exec({"code": "import pandas\nprint('__RESULT__:')\nprint(pandas.Series([1, 2]).sum())", "env": {}})
        check(out["success"] and "3" in json.dumps(out["result"]), f"execute_python: {out['result']}")
    finally:
        t.clean_up()


def prompts():
    print("== prompts")
    for arm, ds in {"A": "manufacturing", "C": "manufacturing_consolidated"}.items():
        system, user = build_prompts(DAB, ds, 14, False)
        check("HINTS:" not in user, f"setup {arm}: no hints, user message {len(user)} chars, "
                                    f"system prompt {len(system)} chars")
        check("safety stock level" in user, f"setup {arm}: question text included")


if __name__ == "__main__":
    for step in (setup_a, setup_c, sandbox, prompts):
        try:
            step()
        except Exception as e:  # report and carry on, so one failure does not hide the rest
            check(False, f"{step.__name__}: {type(e).__name__}: {e}")
    print(f"\n{'DRY RUN PASSED' if not failures else f'{len(failures)} FAILURES'}")
    sys.exit(1 if failures else 0)
