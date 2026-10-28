"""Run one pass of the freshness experiment (FRESHNESS.md §4-6).

One process holds the whole timeline:
  mirror   copies Tapstate's view (views.bank_customer) into bank_live.customer as it changes, and
           records when each replayed transaction first becomes readable there (the lag probe)
  writer   replays the events into the live core banking database (bank_cbs) on the clock, each as
           one Postgres transaction, and runs the nightly warehouse load at each simulated 00:00
  driver   at each checkpoint starts the three arms on the same question, in parallel
A pass starts by rewinding bank_cbs to the end of 30 June and waiting until bank_live matches that
state for every customer; it ends by replaying the rest at full speed and checking that bank_cbs
and bank_live both equal the published dataset.

Agents: --model runs Claude Code through run_claude_arms.run_one, with a hard wall-clock cap of
CAP_S per episode. --scripted runs no model: each arm's answer is computed from that arm's own
database, which checks the plumbing (writer, Tapstate, mirror, warehouse, oracle, scoring).

Logs go to freshness/runs/<label>/: events.jsonl (commit times), lag.jsonl, loads.jsonl,
episodes.jsonl, pass.json.

Usage: python driver.py <dab-root> --pass K (--model claude-opus-5-5 | --scripted) [--speed 1]
                        [--harness ~/bench-ws/harness] [--only 1,5] [--overwrite]
"""
import argparse
import json
import shutil
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fresh_common import (ARMS, RUNS, CBS_DB, COLLECTION, DATASETS, HERE, LIVE_DB, META, QUESTIONS, VIEW,
                          WAREHOUSE_DB, mongo, pg)
import timeline as tl
from common import consolidated_doc, load_customers
from fresh_check import discloses, verdict
from verify_tapstate import as_record
from build_consolidated import verify

DAYS = range(tl.DAYS)


def canonical(doc):
    return {"_id": doc["customer_id"], **consolidated_doc(doc["customer_id"], as_record(doc))}


def account_view(cs):
    """account id -> (balance, set of transaction ids), the part of a state that the replay changes."""
    return {a: (round(x["available_balance"], 2), frozenset(t["reference_id"] for t in x["transactions"]))
            for c in cs.values() for a, x in c["accounts"].items()}


def doc_view(docs):
    return {a["account_id"]: (round(float(a["available_balance"]), 2),
                              frozenset(t["reference_id"] for t in a["transactions"] or []))
            for d in docs for a in d["accounts"]}


PROJECTION = {"customer_id": 1, "accounts.account_id": 1, "accounts.available_balance": 1,
              "accounts.transactions.reference_id": 1}


def wait_converged(coll, expected, timeout, poll=0.25):
    """Wait until coll's balances and transaction ids equal `expected`; returns seconds waited or None."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        if doc_view(coll.find({}, PROJECTION)) == expected:
            return round(time.monotonic() - start, 3)
        time.sleep(poll)
    return None


class Clock:
    """Clock seconds since the pass started, at `speed` times real time. hold_at(t) stops it at t
    (for a bank-wide question) until release()."""

    def __init__(self, speed):
        self.speed, self.t0, self.paused, self.hold = speed, None, 0.0, None
        self.lock = threading.Lock()

    def start(self):
        self.t0 = time.monotonic()

    def raw(self):
        return (time.monotonic() - self.t0 - self.paused) * self.speed

    def now(self):
        with self.lock:
            r = self.raw()
            return min(r, self.hold) if self.hold is not None else r

    def hold_at(self, t):
        with self.lock:
            self.hold = t

    def release(self):
        with self.lock:
            r = self.raw()
            if r > self.hold:
                self.paused += (r - self.hold) / self.speed
            self.hold = None

    def wait_until(self, t, stop=None):
        while self.now() < t:
            if stop is not None and stop.is_set():
                return False
            time.sleep(min(0.05, max(0.001, (t - self.now()) / self.speed)))
        return True


class Log:
    def __init__(self, d):
        self.d, self.lock = d, threading.Lock()
        d.mkdir(parents=True, exist_ok=True)

    def write(self, name, rec):
        with self.lock, open(self.d / f"{name}.jsonl", "a") as f:
            f.write(json.dumps(rec) + "\n")


class Mirror(threading.Thread):
    """views.bank_customer -> bank_live.customer, continuously; plus the lag probe."""

    def __init__(self, log):
        super().__init__(daemon=True)
        self.log, self.stop = log, threading.Event()
        self.ready = threading.Event()
        self.lock = threading.Lock()
        self.pending = defaultdict(dict)   # customer id -> ref -> {"commit": wall, "seen": wall}

    def expect(self, cid, ref):
        with self.lock:
            self.pending[cid][ref] = {"commit": None, "seen": None}

    def committed(self, cid, ref, wall):
        with self.lock:
            p = self.pending[cid].get(ref)
            if p is not None:
                p["commit"] = wall
                self._finish(cid, ref, p)

    def _finish(self, cid, ref, p):
        if p["commit"] is not None and p["seen"] is not None:
            self.log.write("lag", {"ref": ref, "customer_id": cid, "commit": p["commit"], "seen": p["seen"],
                                   "lag_s": round(max(0.0, p["seen"] - p["commit"]), 4)})
            del self.pending[cid][ref]

    def _saw(self, doc, wall):
        cid = doc["customer_id"]
        with self.lock:
            if not self.pending.get(cid):
                return
            present = {t["reference_id"] for a in doc["accounts"] for t in a["transactions"]}
            for ref, p in list(self.pending[cid].items()):
                if ref in present and p["seen"] is None:
                    p["seen"] = wall
                    self._finish(cid, ref, p)

    def run(self):
        m = mongo()
        src, dst = m[VIEW[0]][VIEW[1]], m[LIVE_DB][COLLECTION]
        # open the stream first, then copy everything: nothing between the two is missed, and
        # replaying a change already copied writes the same document again
        with src.watch(full_document="updateLookup") as stream:
            for d in src.find({}, {"_id": 0}):
                dst.replace_one({"_id": d["customer_id"]}, canonical(d), upsert=True)
            self.ready.set()
            while not self.stop.is_set():
                ch = stream.try_next()
                if ch is None:
                    time.sleep(0.01)
                    continue
                op = ch["operationType"]
                if op in ("insert", "update", "replace") and ch.get("fullDocument"):
                    d = {k: v for k, v in ch["fullDocument"].items() if k != "_id"}
                    dst.replace_one({"_id": d["customer_id"]}, canonical(d), upsert=True)
                    self._saw(d, time.time())
                elif op == "delete":
                    self.log.write("mirror", {"warning": "a customer document was deleted", "key": str(ch["documentKey"])})
                elif op in ("drop", "invalidate", "dropDatabase"):
                    self.log.write("mirror", {"error": f"view {op}"})
                    return


def load_warehouse(day, expected, log):
    """The nightly load: copy the view, once it holds the end of the previous day, to bank_warehouse."""
    m = mongo()
    waited = wait_converged(m[VIEW[0]][VIEW[1]], expected, timeout=60)
    docs = [canonical(d) for d in m[VIEW[0]][VIEW[1]].find({}, {"_id": 0})]
    tmp = m[WAREHOUSE_DB][COLLECTION + "_loading"]
    tmp.drop()
    tmp.insert_many(docs)
    tmp.rename(COLLECTION, dropTarget=True)     # readers see the old or the new copy, never a mix
    loaded_at = f"{tl.day_date(day).isoformat()} 00:00"
    m[WAREHOUSE_DB][META].replace_one({"_id": "load"}, {"_id": "load", "loaded_at": loaded_at}, upsert=True)
    log.write("loads", {"day": day, "loaded_at": loaded_at, "wall": time.time(), "converged_after_s": waited,
                        "docs": len(docs)})
    if waited is None:
        log.write("loads", {"warning": f"day {day}: the view had not caught up after 60 s; loaded as it was"})


class Writer(threading.Thread):
    def __init__(self, clock, evts, day_views, mirror, log):
        super().__init__(daemon=True)
        self.clock, self.evts, self.day_views, self.mirror, self.log = clock, evts, day_views, mirror, log
        self.stop, self.flush = threading.Event(), threading.Event()
        self.done = threading.Event()

    def run(self):
        actions = [(e["t"], 1, e) for e in self.evts] + [(tl.load_time(d * tl.BLOCK_S), 0, d) for d in DAYS if d > 0]
        actions.sort(key=lambda a: (a[0], a[1]))
        conn = pg()
        conn.autocommit = False
        cur = conn.cursor()
        for t, kind, x in actions:
            if not self.flush.is_set():
                self.clock.wait_until(t, self.flush)
            if self.stop.is_set():
                return
            if kind == 0:
                if not self.flush.is_set():
                    load_warehouse(x, self.day_views[x], self.log)
                continue
            txn = x["txn"]
            self.mirror.expect(x["customer_id"], x["ref"])
            cur.execute("INSERT INTO transactions (reference_id, account_id, txn_date, description, amount, direction, "
                        "channel, balance_after) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                        (x["ref"], x["account_id"], txn["date"], txn["description"], txn["amount"], txn["direction"],
                         txn["channel"], txn["balance_after"]))
            cur.execute("UPDATE accounts SET available_balance = %s WHERE account_id = %s",
                        (txn["balance_after"], x["account_id"]))
            conn.commit()
            wall = time.time()
            self.mirror.committed(x["customer_id"], x["ref"], wall)
            self.log.write("events", {"ref": x["ref"], "account_id": x["account_id"], "t": x["t"],
                                      "clock": round(self.clock.now(), 3), "commit": wall,
                                      "flush": self.flush.is_set()})
        conn.close()
        self.done.set()


def rewind(cs, evts):
    """bank_cbs as of the end of 30 June. Idempotent."""
    start = tl.state_at(cs, set())
    with pg() as c, c.cursor() as cur:
        cur.execute("DELETE FROM transactions WHERE reference_id = ANY(%s)", ([e["ref"] for e in evts],))
        for cc in start.values():
            for a, x in cc["accounts"].items():
                cur.execute("UPDATE accounts SET available_balance = %s WHERE account_id = %s",
                            (x["available_balance"], a))


def cbs_view():
    with pg() as c, c.cursor() as cur:
        cur.execute("SELECT account_id, available_balance FROM accounts")
        bal = {a: round(float(b), 2) for a, b in cur.fetchall()}
        cur.execute("SELECT account_id, reference_id FROM transactions")
        refs = defaultdict(set)
        for a, r in cur.fetchall():
            refs[a].add(r)
    return {a: (b, frozenset(refs[a])) for a, b in bal.items()}


# --- scripted agents (--scripted): each arm's answer from its own database, no model ------------------

def scripted_answer(arm, q, cs):
    import fresh_questions as fq
    day = q["sim_time"][:10]
    if arm == "D":
        live = cbs_view()
        with pg() as c, c.cursor() as cur:
            cur.execute("SELECT reference_id, account_id, txn_date, description, amount, direction, channel, "
                        "balance_after FROM transactions")
            rows = cur.fetchall()
        txns = defaultdict(list)
        for r in rows:
            txns[r[1]].append({"reference_id": r[0], "date": r[2], "description": r[3], "amount": float(r[4]),
                               "direction": r[5], "channel": r[6], "balance_after": float(r[7])})
        recs = json.loads(json.dumps(cs))    # loans and policies are static: read from upstream
        for cid, c in recs.items():
            for a, x in c["accounts"].items():
                x["available_balance"] = live[a][0]
                # newest first, in the upstream ledger order
                order = {t["reference_id"]: i for i, t in enumerate(cs[cid]["accounts"][a]["transactions"])}
                x["transactions"] = sorted(txns[a], key=lambda t: order[t["reference_id"]])
    else:
        db = LIVE_DB if arm == "UC" else WAREHOUSE_DB
        recs = {d["customer_id"]: as_record(d) for d in mongo()[db][COLLECTION].find({}, {"_id": 0})}
        for cid, c in recs.items():
            for a, x in c["accounts"].items():
                # the documents keep transactions in id order; the templates want newest first
                order = {t["reference_id"]: i for i, t in enumerate(cs[cid]["accounts"][a]["transactions"])}
                x["transactions"] = sorted(x["transactions"], key=lambda t: order[t["reference_id"]])
    if q["kind"] == "portfolio":
        spec = fq.portfolio_spec(q["template"], recs, q["limit"])
    else:
        spec = fq.template(q["template"], q["customer_id"], recs, recs, day)[2]
    if spec[0] == "ids":
        return f"The answer is {spec[1][0]}."
    v = spec[1]
    return f"The answer is {v:,.2f}." if spec[0] == "money" else f"The answer is {v}."


# --- the pass ------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--pass", dest="k", type=int, required=True)
    ap.add_argument("--model")
    ap.add_argument("--scripted", action="store_true")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--harness", type=Path, default=Path.home() / "bench-ws" / "harness")
    ap.add_argument("--only", help="comma-separated question ids (a partial pass, for smoke tests)")
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args()
    if bool(a.model) == a.scripted:
        sys.exit("give exactly one of --model and --scripted")
    dab = a.dab.resolve()
    label = f"{'scripted' if a.scripted else 'cc-' + a.model}_p{a.k}"
    run_dir = RUNS / label
    if run_dir.exists():
        if not a.overwrite:
            sys.exit(f"{run_dir} exists; pass --overwrite to redo this pass")
        shutil.rmtree(run_dir)
    log = Log(run_dir)
    qs = json.loads(QUESTIONS.read_text())
    if a.only:
        keep = {int(x) for x in a.only.split(",")}
        qs = [q for q in qs if q["id"] in keep]

    run_one = None
    if a.model:
        sys.path.insert(0, str(a.harness))
        import run_claude_arms as rca
        rca.run_session = capped(rca.run_session, tl.CAP_S)
        run_one = rca.run_one
        for arm, (ds, _) in ARMS.items():
            for q in qs:
                old = dab / f"query_{ds}" / f"query{q['id']}" / "logs" / "data_agent" / f"{arm}_cc-{a.model}_r{a.k}"
                if old.exists():
                    if not a.overwrite:
                        sys.exit(f"{old} exists; pass --overwrite to redo this pass")
                    shutil.rmtree(old)

    cs = load_customers()
    evts = tl.events(cs)
    day_views = {d: account_view(tl.state_at(cs, tl.applied_by(evts, d * tl.BLOCK_S))) for d in DAYS}
    info = {"label": label, "speed": a.speed, "questions": [q["id"] for q in qs], "started": time.time()}

    print("rewinding bank_cbs to the end of 30 June", flush=True)
    mirror = Mirror(log)
    mirror.start()
    if not mirror.ready.wait(300):
        sys.exit("the mirror did not start")
    rewind(cs, evts)
    assert cbs_view() == day_views[0], "bank_cbs is not at the rewind state"
    waited = wait_converged(mongo()[LIVE_DB][COLLECTION], day_views[0], timeout=600)
    if waited is None:
        sys.exit("bank_live did not reach the rewind state within 10 minutes")
    start_docs = {d["customer_id"]: d for d in mongo()[LIVE_DB][COLLECTION].find({}, {"_id": 0})}
    problems = verify(start_docs, tl.state_at(cs, set()))
    if problems:
        sys.exit(f"bank_live differs from the rewind state: {problems[:5]}")
    info["rewind_converged_s"] = waited
    load_warehouse(0, day_views[0], log)
    print(f"bank_live matches the rewind state for all {len(start_docs)} customers (after {waited} s); "
          "warehouse loaded; starting the clock", flush=True)

    clock = Clock(a.speed)
    writer = Writer(clock, evts, day_views, mirror, log)
    clock.start()
    writer.start()
    for q in sorted(qs, key=lambda q: q["t"]):
        portfolio = q["kind"] == "portfolio"
        if portfolio:
            clock.hold_at(q["t"])
        clock.wait_until(q["t"])
        if portfolio:
            time.sleep(tl.MARGIN_S / a.speed)   # the same catch-up time as any other question's quiet margin
        start_wall, start_clock = time.time(), clock.now()
        print(f"q{q['id']} {q['sim_time']} {q['kind']} {q['template']}", flush=True)

        def episode(arm):
            if a.scripted:
                text, line = scripted_answer(arm, q, cs), "scripted"
            else:
                line = run_one(dab, arm, a.model, q["id"], a.k, a.max_turns, ARMS, tl.CAP_S)
                text = answer_of(dab, arm, a.model, q["id"], a.k, line)
            return arm, text, line

        with ThreadPoolExecutor(len(ARMS)) as pool:
            results = list(pool.map(episode, ARMS))
        end_wall = time.time()
        for arm, text, line in results:
            run = dab / f"query_{ARMS[arm][0]}" / f"query{q['id']}" / "logs" / "data_agent" / f"{arm}_cc-{a.model}_r{a.k}"
            rec = {"q": q["id"], "arm": arm, "kind": q["kind"], "template": q["template"], "sim_time": q["sim_time"],
                   "start": start_wall, "end": end_wall, "clock_start": round(start_clock, 3),
                   "wall_cap": bool(a.model) and (run / "wall_cap.txt").exists(),
                   "verdict": verdict(q, text), "discloses": discloses(text), "result": line, "answer": text}
            log.write("episodes", rec)
            print(f"   {arm:<2} {rec['verdict']:<7} {line[:90]}", flush=True)
        if portfolio:
            clock.release()

    print("replaying the rest at full speed", flush=True)
    writer.flush.set()
    writer.done.wait()
    end_cbs = cbs_view() == account_view(cs)
    waited = wait_converged(mongo()[LIVE_DB][COLLECTION], account_view(cs), timeout=600)
    end_docs = {d["customer_id"]: d for d in mongo()[LIVE_DB][COLLECTION].find({}, {"_id": 0})}
    end_problems = verify(end_docs, cs)
    info.update(finished=time.time(), end_cbs_equals_upstream=end_cbs, end_live_converged_s=waited,
                end_live_problems=end_problems[:20])
    mirror.stop.set()
    (run_dir / "pass.json").write_text(json.dumps(info, indent=1))
    print(f"end check: bank_cbs equals upstream: {end_cbs}; bank_live equals upstream: {not end_problems}", flush=True)
    if not end_cbs or end_problems:
        sys.exit(1)


def capped(run_session, cap):
    """run_claude_arms.run_session with a wall-clock cap: the episode is killed after `cap` seconds."""
    import subprocess

    def run(cmd, user_text, out, idle):
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=out, env=__import__("run_claude_arms").child_env(), text=True)
        try:
            stdout, stderr = proc.communicate(input=user_text, timeout=cap)
            return stdout, stderr, proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            (out / "wall_cap.txt").write_text(f"killed after {cap} s\n")
            return None
    return run


def answer_of(dab, arm, model, q, k, line):
    ds = ARMS[arm][0]
    out = dab / f"query_{ds}" / f"query{q}" / "logs" / "data_agent" / f"{arm}_cc-{model}_r{k}"
    f = out / "answer.json"
    return json.loads(f.read_text())["answer"] if f.exists() else ""


if __name__ == "__main__":
    main()
