"""Run the sales agent once, live, and print its work as it happens: every round trip, every query and
the system it went to, how much data came back, and the tokens spent so far.

The agent is the benchmark's agent: Claude Code with DAB's four tools only (harness/dab_mcp_server.py),
DAB's system prompt, and the dataset's description. What it is connected to is the only difference:
  --setup A   the six live source systems (ERP, PLM, MES, WMS, procurement, costing)
  --setup C   Tapstate's consolidated state (MongoDB mfg_state)
The connections it gets are printed first and appended to its description, so the audience sees
six connection strings in one run and one in the other.

Writes runs/<setup>_<timestamp>/ with stream.jsonl (Claude Code's stream-json output),
tool_calls.jsonl (from the tool server) and summary.json (tokens, round trips, queries per system,
cost, the answer and whether it is correct). trace_report.py turns two such runs into one page.

This is a paid model run (one session, on the Claude Code login on this machine).

Usage: python sales_agent.py <dab-root> --setup A --model claude-opus-5-5 [--query 101]
       python sales_agent.py --replay runs/A_.../stream.jsonl     # re-print a recorded run
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(REPO / "harness"))

PG = f"{os.environ.get('PG_USER', 'postgres')}:{os.environ.get('PG_PASSWORD', 'secret')}@" \
     f"{os.environ.get('PG_HOST', '127.0.0.1')}:{os.environ.get('PG_PORT', '55432')}"
MONGO = f"127.0.0.1:{os.environ.get('MONGO_PORT', '27017')}"

# db name in db_config.yaml -> (system, engine, connection string shown to the agent and the audience)
CONNECTIONS = {
    "A": {
        "erp": ("ERP sales", "PostgreSQL", f"postgresql://{PG}/mfg_erp"),
        "plm": ("PLM", "SQLite", "sqlite:///query_dataset/plm.db"),
        "mes": ("MES", "DuckDB", "duckdb:///query_dataset/mes.duckdb"),
        "wms": ("WMS", "MongoDB", f"mongodb://{MONGO}/mfg_wms"),
        "procurement": ("Procurement", "DuckDB", "duckdb:///query_dataset/procurement.duckdb"),
        "costing": ("Cost accounting", "PostgreSQL", f"postgresql://{PG}/mfg_costing"),
    },
    "C": {
        "mfg_state": ("Tapstate state", "MongoDB", f"mongodb://{MONGO}/mfg_state"),
    },
}
DATASETS = {"A": "manufacturing", "C": "manufacturing_consolidated"}

# terminal colours (off when not a terminal)
TTY = sys.stdout.isatty()
def c(code, s):  # noqa: E302
    return f"\033[{code}m{s}\033[0m" if TTY else str(s)
DIM, BOLD, CYAN, GREEN, RED, YELLOW = "2", "1", "36", "32", "31", "33"  # noqa: E305


def k(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class Trace:
    """Folds Claude Code's stream-json events into round trips, tool calls and token totals."""

    def __init__(self, setup, live=True):
        self.setup, self.live = setup, live
        self.seen_msgs = set()
        self.round_trips = 0
        self.tokens = {"input": 0, "cache_write": 0, "cache_read": 0, "output": 0}
        self.calls = {}          # tool_use id -> call record
        self.order = []
        self.result = None
        self.t0 = time.time()

    @property
    def total(self):
        return sum(self.tokens.values())

    def event(self, ev):
        t = ev.get("type")
        if t == "assistant":
            msg = ev["message"]
            # one model response can arrive as several events (one per content block), same id and usage
            if msg["id"] not in self.seen_msgs:
                self.seen_msgs.add(msg["id"])
                self.round_trips += 1
                u = msg.get("usage") or {}
                self.tokens["input"] += u.get("input_tokens") or 0
                self.tokens["cache_write"] += u.get("cache_creation_input_tokens") or 0
                self.tokens["cache_read"] += u.get("cache_read_input_tokens") or 0
                self.tokens["output"] += u.get("output_tokens") or 0
            for b in msg.get("content") or []:
                if b.get("type") == "tool_use":
                    self.tool_use(b)
        elif t == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    self.tool_result(b)
        elif t == "result":
            self.result = ev

    def tool_use(self, b):
        tool = b["name"].replace("mcp__dab__", "")
        args = b.get("input") or {}
        db = args.get("db_name") or args.get("database") or ""
        system = CONNECTIONS[self.setup].get(db, (db or "-",))[0]
        rec = {"n": len(self.order) + 1, "round_trip": self.round_trips, "tool": tool, "db": db, "system": system,
               "query": args.get("query") or args.get("code") or args.get("answer") or "",
               "tokens_before": self.total, "t": round(time.time() - self.t0, 1)}
        self.calls[b["id"]] = rec
        self.order.append(rec)
        if self.live:
            q = " ".join(str(rec["query"]).split())
            where = f"{system:<16}" if tool == "query_db" or tool == "list_db" else f"{'(python)' if tool == 'execute_python' else '':<16}"
            tag = f"#{rec['n']:>2} rt {self.round_trips:>2}"
            print(f"{c(DIM, tag)}  {c(CYAN, f'{tool:<14}')} {c(BOLD, where)} "
                  f"{q[:90]}{'…' if len(q) > 90 else ''}", flush=True)

    def tool_result(self, b):
        rec = self.calls.get(b.get("tool_use_id"))
        if not rec:
            return
        content = b.get("content")
        text = content if isinstance(content, str) else "".join(x.get("text", "") for x in content or [])
        rec["result_chars"] = len(text)
        rec["error"] = bool(b.get("is_error")) or text.lstrip().lower().startswith(("error", "failed"))
        if self.live:
            mark = c(RED, "error") if rec["error"] else c(GREEN, "ok")
            print(f"{'':>10}  ↳ {mark} {k(len(text))} chars back   {c(YELLOW, f'tokens so far {k(self.total)}')}",
                  flush=True)

    def summary(self):
        by_system = {}
        for r in self.order:
            if r["tool"] in ("query_db", "list_db"):
                by_system[r["system"]] = by_system.get(r["system"], 0) + 1
        res = self.result or {}
        # The per-message usage in the stream is a partial count: output_tokens there misses most of the
        # output (thinking included). The final result event carries the session's real totals, so the
        # summary uses them; the live "tokens so far" counter stays an approximation.
        u = res.get("usage") or {}
        if u:
            self.tokens = {"input": u.get("input_tokens") or 0,
                           "cache_write": u.get("cache_creation_input_tokens") or 0,
                           "cache_read": u.get("cache_read_input_tokens") or 0,
                           "output": u.get("output_tokens") or 0}
        return {
            "setup": self.setup, "round_trips": self.round_trips, "tool_calls": len(self.order),
            "queries": sum(1 for r in self.order if r["tool"] == "query_db"),
            "errors": sum(1 for r in self.order if r.get("error")),
            "queries_by_system": by_system, "systems_touched": len(by_system),
            "tokens": {**self.tokens, "total": self.total},
            "cost_usd": res.get("total_cost_usd"), "duration_s": round((res.get("duration_ms") or 0) / 1000, 1),
            "num_turns": res.get("num_turns"), "calls": self.order,
        }


def banner(setup, model, question):
    print(c(BOLD, f"\n=== Sales agent, setup {setup}: "
                  f"{'six live source systems' if setup == 'A' else 'Tapstate consolidated state'} ({model}) ==="))
    print(c(DIM, "Customer question:"))
    print(f"  {question}\n")
    print(c(DIM, f"Connections given to the agent ({len(CONNECTIONS[setup])}):"))
    for db, (system, engine, uri) in CONNECTIONS[setup].items():
        print(f"  {system:<16} {engine:<11} {uri}")
    print()


def check(dab, ds, q, answer):
    spec = importlib.util.spec_from_file_location("validate", dab / f"query_{ds}" / f"query{q}" / "validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.validate(answer or "")


def finish(trace, out, answer, verdict, question, model):
    s = trace.summary()
    s["answer"], s["correct"], s["check"] = answer, verdict[0], verdict[1]
    s["question"], s["model"] = question, model
    (out / "summary.json").write_text(json.dumps(s, indent=1))
    t = s["tokens"]
    print(c(BOLD, "\n--- Answer ---"))
    print(answer or c(RED, "(no answer)"))
    print(c(GREEN, "correct") if s["correct"] else c(RED, f"not correct: {s['check']}"))
    print(c(BOLD, "\n--- The work ---"))
    print(f"  round trips to the model  {s['round_trips']}")
    print(f"  queries issued            {s['queries']}  ({', '.join(f'{k_}: {v}' for k_, v in s['queries_by_system'].items())})")
    print(f"  failed calls              {s['errors']}")
    print(f"  tokens                    {t['total']:,}  (input {t['input']:,}, cache write {t['cache_write']:,}, "
          f"cache read {t['cache_read']:,}, output {t['output']:,})")
    if s["cost_usd"] is not None:
        print(f"  cost (API-equivalent)     ${s['cost_usd']:.2f}")
    print(f"  wall time                 {s['duration_s']}s")
    print(c(DIM, f"\nsaved {out}"))


def run(a):
    from run_claude_arms import CLAUDE, TOOLS, build_prompts, child_env
    dab = a.dab.resolve()
    ds = DATASETS[a.setup]
    out = (a.out or HERE / "runs") / f"{a.setup}_{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True)
    system_text, user_text = build_prompts(dab, ds, a.query, False)
    conns = "\n".join(f"- {db}: {system} ({engine}) at {uri}" for db, (system, engine, uri) in CONNECTIONS[a.setup].items())
    user_text += f"\n\nConnections (use the names on the left as db_name):\n{conns}\n"
    (out / "system_prompt.txt").write_text(system_text)
    (out / "user_message.txt").write_text(user_text)
    (out / "mcp.json").write_text(json.dumps({"mcpServers": {"dab": {
        "command": sys.executable,
        "args": [str(REPO / "harness" / "dab_mcp_server.py"), "--dab", str(dab), "--dataset", ds, "--run-dir", str(out)],
        "env": {k_: v for k_, v in child_env().items()
                if k_ in ("PATH", "HOME", "DOCKER_HOST", "MONGO_URI", "DAB_PERSISTENT_DBS") or k_.startswith("PG_")},
    }}}))
    question = json.loads((dab / f"query_{ds}" / f"query{a.query}" / "query.json").read_text())
    banner(a.setup, a.model, question)
    cmd = [CLAUDE, "-p", "--output-format", "stream-json", "--verbose", "--model", a.model,
           "--system-prompt-file", str(out / "system_prompt.txt"),
           "--tools", "", "--strict-mcp-config", "--mcp-config", str(out / "mcp.json"),
           "--allowedTools", ",".join(f"mcp__dab__{t}" for t in TOOLS),
           "--setting-sources", "", "--max-turns", str(a.max_turns), "--no-session-persistence"]
    trace = Trace(a.setup)
    with (out / "stream.jsonl").open("w") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                cwd=out, env=child_env(), text=True)
        proc.stdin.write(user_text)
        proc.stdin.close()
        for line in proc.stdout:
            log.write(line)
            try:
                trace.event(json.loads(line))
            except json.JSONDecodeError:
                pass
        proc.wait()
    (out / "claude_stderr.txt").write_text(proc.stderr.read())
    answer_file = out / "answer.json"
    answer = json.loads(answer_file.read_text())["answer"] if answer_file.exists() else ""
    finish(trace, out, answer, check(dab, ds, a.query, answer), question, a.model)


def replay(path, delay):
    """Re-print a recorded run at a readable pace (a fallback when a live run is not wanted)."""
    path = Path(path)
    out = path.parent
    s = json.loads((out / "summary.json").read_text())
    trace = Trace(s["setup"])
    banner(s["setup"], f"recorded run, {s.get('model')}", s.get("question", ""))
    for line in path.read_text().splitlines():
        ev = json.loads(line)
        trace.event(ev)
        if ev.get("type") in ("assistant", "user"):
            time.sleep(delay)
    finish(trace, out, s.get("answer"), (s.get("correct"), s.get("check")), s.get("question"), s.get("model"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path, nargs="?")
    ap.add_argument("--setup", choices=["A", "C"])
    ap.add_argument("--model", default="claude-opus-5-5")
    ap.add_argument("--query", type=int, default=101)
    ap.add_argument("--max-turns", type=int, default=100)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--replay", help="a recorded stream.jsonl to re-print instead of running")
    ap.add_argument("--delay", type=float, default=0.4, help="seconds between events on --replay")
    a = ap.parse_args()
    if a.replay:
        return replay(a.replay, a.delay)
    if not (a.dab and a.setup):
        ap.error("give <dab-root> and --setup, or --replay")
    run(a)


if __name__ == "__main__":
    main()
