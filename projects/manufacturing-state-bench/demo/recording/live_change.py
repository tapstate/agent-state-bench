"""Demo step: a live change, measured. Changes BK-M68B-38's stock at Final Assembly in the WMS (MongoDB)
from its current value to +10, watches the source and Tapstate's live product document
(views.mfg_product) every 20 ms until the change arrives, sets the stock back, watches again, then runs
verify_tapstate.py (no --install) and checks the ten pipelines are running.

Writes recording/live_change.json: every observed state with its time since the start, the measured
latencies, and verify's result. make_video.py renders the split screen from this log, so what the
video shows is what was measured.

Usage (from demo/, with env.sh sourced and TAPSTATE_CLI set): python recording/live_change.py <dab-root>
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
DAB_DIR = HERE.parents[1] / "dab"
STOCK_ID, PN, LOCATION = 61, "BK-M68B-38", 60
PIPELINES = ["mfg_product_ref", "mfg_stock", "mfg_offer", "mfg_routing", "mfg_sales_line", "mfg_po_line",
             "mfg_products", "mfg_work_orders", "mfg_sales_orders", "mfg_purchase_orders"]


def main():
    dab = sys.argv[1]
    src = MongoClient(os.environ.get("MONGO_SOURCES_URI", "mongodb://127.0.0.1:27018/?directConnection=true"))[
        "mfg_wms"]["stock"]
    views = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true"))["views"]
    t0 = time.time()
    log = {"events": [], "steps": []}

    def now():
        return round(time.time() - t0, 3)

    def source_doc():
        d = src.find_one({"_id": STOCK_ID})
        return {k: d[k] for k in ("_id", "item", "location_id", "quantity")}

    def inventory():
        d = views["mfg_product"].find_one({"product_number": PN}, {"inventory": 1, "name": 1})
        return [{"location": i["location"]["name"], "quantity": int(i["quantity"])}
                for i in sorted(d["inventory"], key=lambda i: int(i["location"]["location_id"]))]

    def snap(note):
        log["events"].append({"t": now(), "note": note, "source": source_doc(), "inventory": inventory()})

    def change(new):
        old = source_doc()["quantity"]
        t_set = now()
        src.update_one({"_id": STOCK_ID}, {"$set": {"quantity": new}})
        snap("set")
        while time.time() - t0 - t_set < 60:
            inv = inventory()
            if any(i["quantity"] == new and i["location"] == "Final Assembly" for i in inv):
                break
            time.sleep(0.02)
        else:
            sys.exit(f"the change to {new} did not reach views.mfg_product within 60 s")
        snap("seen")
        lat = round(log["events"][-1]["t"] - t_set, 3)
        log["steps"].append({"from": old, "to": new, "set_at": t_set, "latency_s": lat,
                             "inventory_items": len(log["events"][-1]["inventory"])})
        print(f"{old} -> {new}: views.mfg_product updated after {lat} s", flush=True)

    snap("start")
    original = log["events"][0]["source"]["quantity"]
    time.sleep(2)
    change(original + 10)
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
        status[p] = (r.stdout.splitlines() or [""])[0].split()[-1] if r.stdout else "unknown"
    log["pipelines"] = status
    log["source_restored"] = source_doc()["quantity"] == original
    (HERE / "live_change.json").write_text(json.dumps(log, ensure_ascii=False, indent=1))
    print(f"verify: {log['verify']['line']} ({log['verify']['seconds']} s)")
    print("pipelines: " + ", ".join(f"{k} {v}" for k, v in status.items()))
    if not (log["verify"]["ok"] and log["source_restored"] and all(v == "running" for v in status.values())):
        sys.exit("the stack is not back to its verified state")


if __name__ == "__main__":
    main()
