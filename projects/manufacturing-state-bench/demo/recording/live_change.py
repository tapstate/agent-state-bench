"""Demo step: a live change, measured. Changes one value in a source system, watches the source and
Tapstate's live product document (views.mfg_product) every 20 ms until the change arrives, sets the
value back, watches again, then runs verify_tapstate.py (no --install) and checks that the ten
pipelines are running.

  price (default)  costing (Postgres mfg_costing.item_prices): BK-M68B-38's current list price,
                   2294.99 -> 2199.99 -> back; it reaches the document's list_price through one
                   pipeline (mfg_products). The row has no dates.
  stock            WMS (MongoDB mfg_wms.stock): BK-M68B-38's stock at Final Assembly, +10 and back;
                   through two pipelines (mfg_stock, then mfg_products). Needs pre-images on MongoDB.

Writes recording/live_change.json: every observed state with its time since the start, the measured
latencies and verify's result. make_video.py renders the split screen from this log, so what the
video shows is what was measured.

Usage (from demo/, with env.sh sourced and TAPSTATE_CLI set):
  python recording/live_change.py <dab-root> [price|stock]
"""
import json
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import psycopg2
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
DAB_DIR = HERE.parents[1] / "dab"
PN = "BK-M68B-38"
PIPELINES = ["mfg_product_ref", "mfg_stock", "mfg_offer", "mfg_routing", "mfg_sales_line", "mfg_po_line",
             "mfg_products", "mfg_work_orders", "mfg_sales_orders", "mfg_purchase_orders"]


class Price:
    """costing.item_prices.list_price -> views.mfg_product.list_price"""
    system, engine, table = "costing", "postgres", "mfg_costing.item_prices"
    pipelines = ["mfg_products"]

    def __init__(self):
        self.con = psycopg2.connect(dbname="mfg_costing", host=os.environ.get("PG_HOST", "127.0.0.1"),
                                    port=int(os.environ.get("PG_PORT", "55432")), user=os.environ.get("PG_USER", "postgres"),
                                    password=os.environ.get("PG_PASSWORD", "secret"))
        self.con.autocommit = True

    def row(self):
        cur = self.con.cursor()
        cur.execute("SELECT product_number, standard_cost, list_price FROM item_prices WHERE product_number = %s", (PN,))
        pn, sc, lp = cur.fetchone()
        return {"product_number": pn, "standard_cost": float(sc), "list_price": float(lp)}

    def value(self):
        return self.row()["list_price"]

    def target(self, original):
        return 2199.99

    def statement(self, v):
        return f"UPDATE item_prices SET list_price = {v:.2f} WHERE product_number = '{PN}';"

    def set(self, v):
        self.con.cursor().execute("UPDATE item_prices SET list_price = %s WHERE product_number = %s",
                                  (Decimal(f"{v:.6f}"), PN))

    @staticmethod
    def state(doc):
        return {"list_price": float(doc["list_price"]), "standard_cost": float(doc["standard_cost"])}

    @staticmethod
    def arrived(st, v):
        return abs(st["list_price"] - v) < 1e-6


class Stock:
    """wms.stock.quantity (Final Assembly) -> views.mfg_product.inventory"""
    system, engine, table = "wms", "mongodb", "mfg_wms.stock"
    pipelines = ["mfg_stock", "mfg_products"]
    STOCK_ID = 61

    def __init__(self):
        self.col = MongoClient(os.environ.get("MONGO_SOURCES_URI", "mongodb://127.0.0.1:27018/?directConnection=true"))[
            "mfg_wms"]["stock"]

    def row(self):
        d = self.col.find_one({"_id": self.STOCK_ID})
        return {k: d[k] for k in ("_id", "item", "location_id", "quantity")}

    def value(self):
        return self.row()["quantity"]

    def target(self, original):
        return original + 10

    def statement(self, v):
        return f"db.stock.updateOne({{ _id: {self.STOCK_ID} }}, {{ $set: {{ quantity: {v} }} }})"

    def set(self, v):
        self.col.update_one({"_id": self.STOCK_ID}, {"$set": {"quantity": v}})

    @staticmethod
    def state(doc):
        return {"inventory": [{"location": i["location"]["name"], "quantity": int(i["quantity"])}
                              for i in sorted(doc["inventory"], key=lambda i: int(i["location"]["location_id"]))]}

    @staticmethod
    def arrived(st, v):
        return any(i["quantity"] == v and i["location"] == "Final Assembly" for i in st["inventory"])


def main():
    dab = sys.argv[1]
    kind = sys.argv[2] if len(sys.argv) > 2 else "price"
    sc = {"price": Price, "stock": Stock}[kind]()
    views = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true"))["views"]
    t0 = time.time()
    log = {"kind": kind, "system": sc.system, "engine": sc.engine, "table": sc.table, "pipelines": sc.pipelines,
           "events": [], "steps": []}

    def now():
        return round(time.time() - t0, 3)

    def live():
        return sc.state(views["mfg_product"].find_one({"product_number": PN}))

    def snap(note, statement=None):
        log["events"].append({"t": now(), "note": note, "statement": statement, "source": sc.row(), "state": live()})

    def change(new):
        old = sc.value()
        t_set = now()
        sc.set(new)
        snap("set", sc.statement(new))
        while time.time() - t0 - t_set < 60:
            if sc.arrived(live(), new):
                break
            time.sleep(0.02)
        else:
            sys.exit(f"the change to {new} did not reach views.mfg_product within 60 s")
        snap("seen", sc.statement(new))
        lat = round(log["events"][-1]["t"] - t_set, 3)
        log["steps"].append({"from": old, "to": new, "set_at": t_set, "latency_s": lat})
        print(f"{old} -> {new}: views.mfg_product updated after {lat} s", flush=True)

    snap("start")
    original = sc.value()
    time.sleep(2)
    change(sc.target(original))
    time.sleep(3)
    change(original)
    time.sleep(1)
    started = time.time()
    v = subprocess.run([sys.executable, "verify_tapstate.py", dab], cwd=DAB_DIR, capture_output=True, text=True)
    out = (v.stdout + v.stderr).strip().splitlines()
    log["verify"] = {"ok": v.returncode == 0, "line": out[-1] if out else "", "seconds": round(time.time() - started)}
    cli = os.environ.get("TAPSTATE_CLI", "tapstate")
    status = {}
    for p in PIPELINES:
        r = subprocess.run([cli, "-c", os.environ["TAPSTATE_SERVER"], "-u", "admin", "status", p],
                           capture_output=True, text=True)
        status[p] = r.stdout.splitlines()[0].split()[-1] if r.stdout.strip() else "unknown"
    log["pipeline_status"] = status
    log["source_restored"] = sc.value() == original
    (HERE / "live_change.json").write_text(json.dumps(log, ensure_ascii=False, indent=1, default=str))
    print(f"verify: {log['verify']['line']} ({log['verify']['seconds']} s)")
    print("pipelines: " + ", ".join(f"{k} {v}" for k, v in status.items()))
    if not (log["verify"]["ok"] and log["source_restored"] and all(v == "running" for v in status.values())):
        sys.exit("the stack is not back to its verified state")


if __name__ == "__main__":
    main()
