"""Dry run of the bank datasets through DAB's own tools, with no model calls.

For setups A/B (query_bank) and setup C (query_bank_consolidated):
  1. DAB's loader (the runners' warm step) finds every server-side database in place.
  2. list_db on every database, through DAB's ListDBTool.
  3. Six reference questions answered with real queries through DAB's QueryDBTool: in A/B
     by joining the systems on their own key formats, in C with MongoDB aggregations. Each
     answer is scored by that question's own validate.py.
  4. One execute_python call in DAB's sandbox container.
Then builds the three setups' prompts exactly as run_claude_arms.py does and checks that only
setup B carries the hints.

Run with the docker group active (the sandbox is a container), from this directory:
  sg docker -c "python dry_run.py <dab-root> <bench-harness-dir>"
"""
import importlib.util
import json
import os
import sys
from pathlib import Path

DAB = Path(sys.argv[1]).resolve()
HARNESS = Path(sys.argv[2]).resolve()
os.environ.update({"DAB_PERSISTENT_DBS": "1", "PG_HOST": "127.0.0.1", "PG_PORT": "55432", "PG_USER": "postgres",
                   "PG_PASSWORD": "secret", "MONGO_URI": "mongodb://127.0.0.1:27017/?directConnection=true"})
os.environ["PATH"] = f"{HARNESS / 'bin'}:{os.environ['PATH']}"
os.chdir(DAB)
sys.path[:0] = [str(DAB), str(HARNESS)]

from common_scaffold.tools.ExecTool import ExecTool  # noqa: E402
from common_scaffold.tools.ListDBTool import ListDBTool  # noqa: E402
from common_scaffold.tools.QueryDBTool import QueryDBTool  # noqa: E402
from run_arms import warm  # noqa: E402
from run_claude_arms import build_prompts  # noqa: E402

LOG = Path("/tmp") / "bank_dry_run_tools.jsonl"
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
    check(ok, f"q{q} answered through query_db: {answer!r} ({reason})")


def one(rows):
    return list(rows[0].values())[0]


def setup_ab():
    ds = "bank"
    print(f"== query_{ds} (setups A and B)")
    warm(DAB, [ds])
    check(True, "DAB loader found bank_cbs and bank_crm in place")
    q, ls = tools(ds)
    for db in ("core_banking", "cards", "lending", "payments", "crm", "insurance"):
        out = ls.exec({"db_name": db})
        check(out["success"] and len(out["result"]) > 0, f"list_db {db}: {out['result']}")

    score(ds, 2, str(one(run(q, "cards", "SELECT count(*) FROM cards WHERE card_type='CREDIT' AND status='frozen'"))))
    total = float(one(run(q, "lending", "SELECT sum(outstanding) FROM loans WHERE loan_type='home_loan'")))
    score(ds, 3, f"{total:.2f}")
    r = run(q, "payments", "SELECT payee, count(*) AS n FROM mandates WHERE status='paused' "
                           "GROUP BY payee ORDER BY n DESC LIMIT 1")[0]
    score(ds, 4, f"{r['payee']}, {r['n']} paused mandates")
    # q6: senior citizens (core banking) with a gold loan (lending, borrower_ref = lower-case suffix)
    seniors = {r["customer_id"] for r in run(q, "core_banking", "SELECT customer_id FROM customers WHERE senior_citizen")}
    gold = {"CUST_" + r["borrower_ref"].upper()
            for r in run(q, "lending", "SELECT DISTINCT borrower_ref FROM loans WHERE loan_type='gold_loan'")}
    score(ds, 6, str(len(seniors & gold)))
    # q9: open requests (crm) on accounts of customers aged 60+ (core banking)
    accs = {r["account_id"] for r in run(q, "core_banking", "SELECT a.account_id FROM accounts a JOIN customers c "
                                                             "USING (customer_id) WHERE c.age >= 60")}
    reqs = run(q, "crm", {"collection": "service_requests", "filter": {"status": "OPEN"},
                          "projection": {"account_id": 1, "_id": 0}})
    score(ds, 9, str(sum(1 for r in reqs if r["account_id"] in accs)))
    # q16: savings accounts (core banking) with a frozen card (cards, integer account_no) and an active
    # UPI autopay mandate (payments, IFSC/account)
    sav = {r["account_id"] for r in run(q, "core_banking", "SELECT account_id FROM accounts WHERE account_type='SAVINGS'")}
    by_no = {int(a[2:]): a for a in sav}
    # DAB's query tool returns every value as a string, on every engine
    frozen = {by_no[int(r["account_no"])] for r in run(q, "cards", "SELECT DISTINCT account_no FROM cards "
                                                                    "WHERE status='frozen'")
              if int(r["account_no"]) in by_no}
    upi = {r["debit_account"].split("/", 1)[1] for r in run(
        q, "payments", "SELECT DISTINCT debit_account FROM mandates WHERE status='active' AND mandate_type='UPI_AUTOPAY'")}
    score(ds, 16, str(len(frozen & upi & sav)))


def setup_c():
    ds = "bank_consolidated"
    print(f"== query_{ds} (setup C)")
    warm(DAB, [ds])
    check(True, "DAB loader found bank_state in place")
    q, ls = tools(ds)
    out = ls.exec({"db_name": "bank_state"})
    check(out["success"] and "customer" in json.dumps(out["result"]), f"list_db bank_state: {out['result']}")

    def agg(pipeline):
        return run(q, "bank_state", {"collection": "customer", "pipeline": pipeline})

    score(ds, 2, str(one(agg([{"$unwind": "$cards"}, {"$match": {"cards.card_type": "CREDIT", "cards.status": "frozen"}},
                               {"$count": "n"}]))))
    total = one(agg([{"$unwind": "$loans"}, {"$match": {"loans.loan_type": "home_loan"}},
                     {"$group": {"_id": None, "s": {"$sum": "$loans.outstanding"}}}, {"$project": {"_id": 0}}]))
    score(ds, 3, f"{float(total):.2f}")
    r = agg([{"$unwind": "$mandates"}, {"$match": {"mandates.status": "paused"}},
             {"$group": {"_id": "$mandates.payee", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}, {"$limit": 1}])[0]
    score(ds, 4, f"{r['_id']}, {r['n']} paused mandates")
    score(ds, 6, str(one(agg([{"$match": {"profile.senior_citizen": True, "loans.loan_type": "gold_loan"}},
                               {"$count": "n"}]))))
    score(ds, 9, str(one(agg([{"$match": {"profile.age": {"$gte": 60}}}, {"$unwind": "$requests"},
                               {"$match": {"requests.status": "OPEN"}}, {"$count": "n"}]))))

    def has(arr, conds):
        return {"$gt": [{"$size": {"$filter": {"input": f"${arr}", "as": "x", "cond": {"$and": conds}}}}, 0]}

    score(ds, 16, str(one(agg([
        {"$unwind": "$accounts"}, {"$match": {"accounts.account_type": "SAVINGS"}},
        {"$match": {"$expr": {"$and": [
            has("cards", [{"$eq": ["$$x.status", "frozen"]}, {"$eq": ["$$x.linked_account", "$accounts.account_id"]}]),
            has("mandates", [{"$eq": ["$$x.status", "active"]}, {"$eq": ["$$x.type", "UPI_AUTOPAY"]},
                             {"$eq": ["$$x.account_id", "$accounts.account_id"]}])]}}},
        {"$count": "n"}]))))


def sandbox():
    print("== execute_python sandbox")
    work = Path("/tmp/bank_dry_run_exec")
    work.mkdir(exist_ok=True)
    t = ExecTool(log_path=LOG, name="execute_python", work_dir=work, timeout=120)
    try:
        out = t.exec({"code": "import pandas\nprint('__RESULT__:')\nprint(pandas.Series([1, 2]).sum())", "env": {}})
        check(out["success"] and "3" in json.dumps(out["result"]), f"execute_python: {out['result']}")
    finally:
        t.clean_up()


def prompts():
    print("== prompts")
    hint = "HINTS:"
    for arm, (ds, hints) in {"A": ("bank", False), "B": ("bank", True), "C": ("bank_consolidated", False)}.items():
        system, user = build_prompts(DAB, ds, 14, hints)
        check((hint in user) == (arm == "B"), f"setup {arm}: hints {'present' if hint in user else 'absent'}, "
                                              f"user message {len(user)} chars, system prompt {len(system)} chars")
        check("CUST_08796420" in user, f"setup {arm}: question text included")


if __name__ == "__main__":
    for step in (setup_ab, setup_c, sandbox, prompts):
        try:
            step()
        except Exception as e:  # report and carry on, so one failure does not hide the rest
            check(False, f"{step.__name__}: {type(e).__name__}: {e}")
    print(f"\n{'DRY RUN PASSED' if not failures else f'{len(failures)} FAILURES'}")
    sys.exit(1 if failures else 0)
