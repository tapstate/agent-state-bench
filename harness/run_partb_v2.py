"""Part B v2 orchestrator: does consolidated state have to be continuous, or is a periodic copy enough?

A TPC-C workload (go-tpc against Postgres `tpccsrc`) stands in for time: `--tx-per-day` transactions
make one simulated day, so a lag of 15 minutes, 1 hour, 1 day or 1 week is a number of transactions.
For each question time T:

  1. take the copies: run the workload up to T - 1 week, snapshot; up to T - 1 day, snapshot; and so
     on for 1 hour and 15 minutes; each snapshot is a batch reload of the Tapstate views, checked
     against the source, then copied to its own database (setups S4, S3, S2, S1)
  2. run the last 15 minutes of workload, reload and check the views again: that is C at T
  3. draw the 15 questions with this T's seed and compute their ground truth at T from the source
  4. ask every question of C and of each copy; the workload waits until all runs are done

Snapshots are batch reloads rather than CDC because a long CDC run stalls on the tested server
(tapstate/tapstate#526); C's own freshness under continuous CDC is not what this measures.

Usage: python run_partb_v2.py <dab-root> <stack-dir> [--model claude-sonnet-5] [--runs 3]
                              [--times 3] [--tx-per-day 2000]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "datasets" / "partb_v2"))
import questions as Q  # noqa: E402
from run_all import run_phase  # noqa: E402

MONGO = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=tpccsrc"
VIEWS = ["customer", "district", "item", "order"]
LAGS = [("S4", "1 week", 7 * 24 * 60), ("S3", "1 day", 24 * 60), ("S2", "1 hour", 60), ("S1", "15 minutes", 15)]

DESCRIPTION = """You are working with one database, {db}, stored in MongoDB.

{db} holds the consolidated state of a wholesale supplier's order-processing system: warehouses,
sales districts, customers, orders, deliveries and stock. {freshness}

Guarantees:
- One document per entity, identified by `pk` (the entity's key columns joined with '-').
- Related records are embedded: a customer's orders in the customer, an order's lines in the
  order, a district's undelivered orders in the district, an item's stock record per warehouse
  in the item.

Collections in {db}:
- customer: one document per customer
  Fields: pk, c_w_id, c_d_id, c_id, c_first, c_middle, c_last, c_credit (GC good / BC bad),
  c_credit_lim, c_discount, c_balance, c_ytd_payment, c_payment_cnt, c_delivery_cnt, c_since,
  address and phone fields
  - orders: array of the customer's orders (headers only): o_id, o_entry_d, o_carrier_id (null
    until delivered), o_ol_cnt
- order: one document per order
  Fields: pk, o_w_id, o_d_id, o_id, o_c_id (customer), o_entry_d, o_carrier_id (null until
  delivered), o_ol_cnt, o_all_local
  - lines: array of order lines: ol_number, ol_i_id (item), ol_supply_w_id, ol_quantity,
    ol_amount, ol_delivery_d (null until delivered)
- district: one document per district
  Fields: pk, d_w_id, d_id, d_name, d_tax, d_ytd (year-to-date payments), d_next_o_id (the id the
  next new order will get)
  - backlog: array of the district's orders waiting for delivery: no_o_id
- item: one document per item
  Fields: pk, i_id, i_name, i_price, i_data
  - stock: array of the item's stock record per warehouse: s_w_id, s_quantity, s_ytd (units sold
    year to date), s_order_cnt, s_remote_cnt
"""
LIVE = "It is maintained continuously from the operational database."
COPY = "It is copied from the operational database by a scheduled batch job."


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


class Stack:
    def __init__(self, path):
        self.path = Path(path)

    def cli(self, *commands):
        return subprocess.run([str(self.path / "tsx.sh"), "tpccws", *commands], capture_output=True,
                              text=True).stdout

    def stop_all(self):
        for v in VIEWS:
            self.cli(f"stop {v}_state -y")

    def workload(self, n):
        """Run n TPC-C transactions with the pipelines stopped: every copy is taken by a reload."""
        if n <= 0:
            return
        self.stop_all()
        subprocess.run([str(self.path.parent / "keep" / "tools" / "go-tpc"), "tpcc", "run", "--driver", "postgres",
                        "-H", "127.0.0.1", "-P", "55432", "-U", "postgres", "-p", "secret", "-D", "tpccsrc",
                        "--warehouses", "1", "--conn-params", "sslmode=disable", "-T", "1", "--count", str(n)],
                       check=True, capture_output=True)

    def reload(self):
        """Reload every view from scratch on a fresh replication slot, one view at a time, each checked
        against the source before the next starts; a view that does not match within 10 minutes is
        reloaded again (up to 3 attempts). Starting all four at once stalled the largest one."""
        self.stop_all()
        t0 = time.time()
        for v in VIEWS:
            for attempt in range(3):
                self.cli(f"stop {v}_state -y")
                self.drop_free_slots()
                MONGO["views"][v].drop()
                self.cli(f"start {v}_state")
                t1 = time.time()
                while time.time() - t1 < 600 and v in mismatches([v]):
                    time.sleep(10)
                if v not in mismatches([v]):
                    break
                log(f"reload: {v} did not match the source after 10 minutes (attempt {attempt + 1}); retrying")
            else:
                raise SystemExit(f"view {v} did not match the source after 3 reloads")
        return time.time() - t0

    def drop_free_slots(self):
        cur = psycopg2.connect(DSN).cursor()
        for _ in range(12):
            time.sleep(5)
            cur.execute("select count(pg_drop_replication_slot(slot_name)) from pg_replication_slots "
                        "where database = 'tpccsrc' and not active")
            cur.connection.commit()
            cur.execute("select count(*) from pg_replication_slots where database = 'tpccsrc' and not active")
            if cur.fetchone()[0] == 0:
                return


def num(v):
    """A stored number as a plain Decimal: decimal columns arrive in MongoDB as Decimal128."""
    return v.to_decimal() if hasattr(v, "to_decimal") else v


def mismatches(views=None):
    """Views whose documents differ from the source on the fields the workload changes."""
    cur = psycopg2.connect(DSN).cursor()
    want, got = {}, {}
    cur.execute("select c_w_id||'-'||c_d_id||'-'||c_id, round(c_balance::numeric,2)::text, c_payment_cnt from customer")
    want["customer"] = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    got["customer"] = {d["pk"]: (f"{num(d['c_balance']):.2f}", d["c_payment_cnt"])
                       for d in MONGO["views"]["customer"].find({}, {"pk": 1, "c_balance": 1, "c_payment_cnt": 1})}
    cur.execute("select o_w_id||'-'||o_d_id||'-'||o_id, o_carrier_id, "
                "(select count(*) from order_line where ol_w_id=o_w_id and ol_d_id=o_d_id and ol_o_id=o_id) from orders")
    want["order"] = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    got["order"] = {d["pk"]: (d.get("o_carrier_id"), len(d.get("lines", [])))
                    for d in MONGO["views"]["order"].find({}, {"pk": 1, "o_carrier_id": 1, "lines.ol_number": 1})}
    cur.execute("select d_w_id||'-'||d_id, d_next_o_id, "
                "(select count(*) from new_order where no_w_id=d_w_id and no_d_id=d_id) from district")
    want["district"] = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    got["district"] = {d["pk"]: (d["d_next_o_id"], len(d.get("backlog", [])))
                       for d in MONGO["views"]["district"].find({}, {"pk": 1, "d_next_o_id": 1, "backlog.no_o_id": 1})}
    cur.execute("select s_i_id::text, s_quantity, s_ytd from stock where s_w_id = 1")
    want["item"] = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    got["item"] = {d["pk"]: next(((s["s_quantity"], s["s_ytd"]) for s in d.get("stock", []) if s["s_w_id"] == 1), None)
                   for d in MONGO["views"]["item"].find({}, {"pk": 1, "stock": 1})}
    return [v for v in want if (views is None or v in views) and want[v] != got[v]]


def snapshot(db):
    MONGO.drop_database(db)
    for v in VIEWS:
        MONGO["views"][v].aggregate([{"$project": {"_id": 0}}, {"$out": {"db": db, "coll": v}}])
        MONGO[db][v].create_index("pk")


def write_dirs(dab, name, qs, dbs):
    """dbs: arm suffix -> (mongo db, live?). One DAB-style dataset directory per arm."""
    for suffix, (db, live) in dbs.items():
        d = dab / f"query_{name}{suffix}"
        for q in qs:
            Q.write_query_dir(d / f"query{q['id']}", q)
        (d / "query_dataset" / "none").mkdir(parents=True, exist_ok=True)
        (d / "db_config.yaml").write_text(
            f"db_clients:\n  {db}:\n    db_type: mongo\n    db_name: {db}\n    dump_folder: query_dataset/none\n")
        (d / "db_description.txt").write_text(DESCRIPTION.format(db=db, freshness=LIVE if live else COPY))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("stack", type=Path)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--times", type=int, default=3)
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--tx-per-day", type=int, default=2000)
    ap.add_argument("--wait-min", type=int, default=30)
    a = ap.parse_args()
    stack, record = Stack(a.stack), a.dab / "partb_v2_times.jsonl"
    per_min = a.tx_per_day / (24 * 60)
    for k in range(a.start, a.times):
        name, taken = f"tpcc_t{k}", {}
        done = 0
        for i, (arm, label, minutes) in enumerate(LAGS):
            # run the workload up to T - lag (the first copy of each T also covers the gap since the last T)
            target = round((LAGS[0][2] - minutes) * per_min)
            stack.workload(target - done)
            done = target
            secs = stack.reload()
            db = f"{name}_{arm.lower()}"
            snapshot(db)
            taken[arm] = {"db": db, "lag": label, "tx_before_T": round(minutes * per_min), "reload_s": round(secs)}
            log(f"{name}: copy {arm} ({label} before T) taken; reload {secs:.0f}s")
        stack.workload(round(LAGS[0][2] * per_min) - done)
        secs = stack.reload()
        snapshot(f"{name}_c")
        cur = psycopg2.connect(DSN).cursor()
        qs = Q.instantiate(cur, seed=k)
        write_dirs(a.dab, name, qs, {"_consolidated": (f"{name}_c", True),
                                     **{f"_lag{arm[1]}": (t["db"], False) for arm, t in taken.items()}})
        with record.open("a") as f:
            f.write(json.dumps({"time": k, "tx_per_day": a.tx_per_day, "copies": taken, "reload_c_s": round(secs),
                                "questions": [{"id": q["id"], "velocity": q["velocity"], "text": q["text"],
                                               "truth": q["truth"]} for q in qs]}, default=str) + "\n")
        log(f"{name}: C taken; questions written")
        while True:
            ok, skip, not_run, failed, auth, rc = run_phase(a.dab, a.model, name, "C,S1,S2,S3,S4",
                                                              f"1-{len(Q.QUESTIONS)}", a.runs)
            log(f"{name}: ok={ok} already={skip} not_run={not_run} failed={failed} auth_failed={auth}")
            if auth:
                log(f"authentication failed: log in again, then rerun with --from {k}")
                return 2
            if rc != 0 and not (ok or skip):
                log(f"{name}: the runner failed before running anything; stopping (rerun with --from {k})")
                return 1
            if not_run or failed:
                time.sleep(a.wait_min * 60 if not_run else 10)
                continue
            break
    log("all question times done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
