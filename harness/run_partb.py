"""Part B orchestrator: replay crmarenapro checkpoint by checkpoint and ask every setup at each one.

Per checkpoint k:
  1. apply batch k to the live source database `crm` (row-level writes; CDC carries them)
  2. wait until Tapstate's views match the source, and record how long that took
  3. at k = 0 only: freeze a copy of the views (setup S, a stale batch copy)
  4. export the raw sources in DAB's original six-database layout (setups A and B)
  5. write each question's directory with this checkpoint's ground truth, for A/B, C and S
  6. run every question in every setup; the next batch waits until all of them are done

The Part B Tapstate pipelines must be running (gen_crm_pipelines.py <ws> --partb) and the
`views` database must hold only their collections.

Usage: python run_partb.py <dab-root> [--model claude-sonnet-5] [--runs 3] [--from 0] [--to 5]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from pymongo import MongoClient

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "datasets"))
import partb_crm as P  # noqa: E402
import partb_questions as Q  # noqa: E402
from gen_crm_pipelines import PARTB_VIEWS, VIEWS as ALL_VIEWS  # noqa: E402

VIEWS = {v: ALL_VIEWS[v] for v in PARTB_VIEWS}
from run_all import run_phase  # noqa: E402

MONGO = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")
FROZEN_DB = "crmlive_frozen"


def log(msg):
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def source_signature():
    """What the views must show: per root table, its ids and the fields the batches change."""
    cur = P.pg("crm").cursor()
    sig = {}
    for view, (root, _) in VIEWS.items():
        cols = {"support_case": "status, ownerid, priority, closeddate", "lead": "status"}.get(root)
        cur.execute(f'select id{", " + cols if cols else ""} from "{root}"')
        sig[view] = {P.key(r[0]): tuple((v or "").strip().lstrip("#") if isinstance(v, str) else v for v in r[1:])
                     for r in cur.fetchall()}
    return sig


def view_signature(db):
    sig = {}
    for view, (root, _) in VIEWS.items():
        fields = {"support_case": ["status", "ownerid", "priority", "closeddate"], "lead": ["status"]}.get(root, [])
        sig[view] = {}
        for d in MONGO[db][view].find({}, {"_id": 0, "id": 1, **{f: 1 for f in fields}}):
            sig[view][P.key(d["id"])] = tuple((d.get(f) or "").strip().lstrip("#") if isinstance(d.get(f), str) else d.get(f)
                                              for f in fields)
    return sig


class NotSynced(SystemExit):
    pass


def wait_synced(timeout=1800):
    want = source_signature()
    t0 = time.time()
    while time.time() - t0 < timeout:
        got = view_signature("views")
        bad = [v for v in want if want[v] != got.get(v)]
        if not bad:
            return time.time() - t0
        time.sleep(2)
    raise NotSynced(f"views did not catch up within {timeout}s: {bad}")


def freeze():
    MONGO.drop_database(FROZEN_DB)
    for view in VIEWS:
        MONGO["views"][view].aggregate([{"$project": {"_id": 0}}, {"$out": {"db": FROZEN_DB, "coll": view}}])


def description(db, live):
    src = (HERE.parent / "datasets" / "descriptions" / "armC_db_description.txt").read_text()
    src = src.replace("crm_state", db)
    head, _, body = src.partition("Collections in")
    first, _, rest = body.partition("\n")
    blocks, cur = [], None
    for line in rest.splitlines():
        if line.startswith("- "):
            cur = [line]
            blocks.append(cur)
        elif cur is not None:
            cur.append(line)
    keep = [b for b in blocks if b[0][2:].split(":")[0] in VIEWS]
    src = head + "Collections in" + first + "\n" + "\n".join(l for b in keep for l in b) + "\n"
    if not live:
        src = src.replace("It is maintained continuously from\nthe company's operational databases.",
                          "It is copied from the company's operational databases by a batch job.")
    return src


def write_dirs(dab, k):
    asof = P.CHECKPOINTS[k]
    cur = P.pg("crm").cursor()
    raw = dab / f"query_crmlive_cp{k}"
    dirs = {raw: None,
            dab / f"query_crmlive_cp{k}_consolidated": ("views", True),
            dab / f"query_crmlive_cp{k}_frozen": (FROZEN_DB, False)}
    truths = {}
    for q in Q.QUESTIONS:
        truth = Q.ground_truth(cur, q, asof)
        truths[q["id"]] = truth
        for d in dirs:
            Q.write_query_dir(d / f"query{q['id']}", q, asof, truth)
    for d, spec in dirs.items():
        if spec is None:
            continue
        db, live = spec
        (d / "query_dataset" / "none").mkdir(parents=True, exist_ok=True)  # the config loader requires the path
        (d / "db_config.yaml").write_text(
            f"db_clients:\n  {db}:\n    db_type: mongo\n    db_name: {db}\n    dump_folder: query_dataset/none\n")
        (d / "db_description.txt").write_text(description(db, live))
    return truths


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--model", default="claude-sonnet-5")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--from", dest="start", type=int, default=0)
    ap.add_argument("--to", type=int, default=len(P.CHECKPOINTS) - 1)
    ap.add_argument("--wait-min", type=int, default=30)
    ap.add_argument("--repair", help="a command that brings stalled views back in line with the source; "
                    "run when they do not catch up, and the checkpoint is recorded as repaired, with no sync time")
    a = ap.parse_args()
    record = a.dab / "partb_checkpoints.jsonl"
    for k in range(a.start, a.to + 1):
        counts = P.apply(k)
        applied = time.time()
        repaired = False
        try:
            lag = wait_synced()
            log(f"cp{k} {P.CHECKPOINTS[k]}: applied {counts}; views caught up in {lag:.1f}s")
        except NotSynced as e:
            if not a.repair:
                raise
            log(f"cp{k} {P.CHECKPOINTS[k]}: applied {counts}; {e}; repairing")
            subprocess.run(a.repair, shell=True, check=True)
            wait_synced(timeout=120)
            lag, repaired = None, True
            log(f"cp{k}: views repaired and in line with the source")
        if k == 0:
            freeze()
        P.export(k, a.dab)
        P.verify_export(k, a.dab)
        truths = write_dirs(a.dab, k)
        with record.open("a") as f:
            f.write(json.dumps({"checkpoint": k, "asof": P.CHECKPOINTS[k], "applied_at": applied,
                                "changes": counts, "sync_seconds": lag, "repaired": repaired,
                                "truth": truths}) + "\n")
        while True:
            ok, skip, not_run, failed, auth, rc = run_phase(a.dab, a.model, f"crmlive_cp{k}", "A,B,C,S",
                                                              f"1-{len(Q.QUESTIONS)}", a.runs)
            log(f"cp{k}: ok={ok} already={skip} not_run={not_run} failed={failed} auth_failed={auth}")
            if auth:
                log("authentication failed: log in again, then rerun with --from " + str(k))
                return 2
            if rc != 0 and not (ok or skip):
                log(f"cp{k}: the runner failed before running anything; stopping (rerun with --from {k})")
                return 1
            if not_run or failed:
                time.sleep(a.wait_min * 60 if not_run else 10)
                continue
            break
    log("all checkpoints done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
