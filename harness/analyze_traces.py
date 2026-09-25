"""Where do a run's tokens go? Break Claude Code runs down by tool-call kind.

For every finished run (claude_result.json + tool_calls.jsonl): tokens, turns, and each tool
call classified as
  explore  list_db, schema / sample / distinct-value probes, small-limit peeks
  fetch    other query_db calls (the data the answer is computed from)
  python   execute_python that succeeded
  error    any failed call (bad SQL/Mongo, Python exceptions, missing __RESULT__ print)
  answer   return_answer
plus the characters every result put into the context (it is re-read on every later turn,
so a large early result is paid for many times).

Usage: python analyze_traces.py <dab-root> [--model cc-claude-sonnet-5] [--run 0]
"""
import argparse
import json
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path

EXPLORE = re.compile(r"information_schema|pragma|describe |show tables|distinct|limit\s+[1-9]\b|"
                     r'"limit"\s*:\s*[1-9]\b|\$count|count\(\*\)', re.I)


def kind(call):
    if not call["success"]:
        return "error"
    t = call["tool"]
    if t == "list_db":
        return "explore"
    if t == "return_answer":
        return "answer"
    if t == "execute_python":
        return "python"
    return "explore" if EXPLORE.search(call["args"].get("query", "")) else "fetch"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--model", default="cc-claude-sonnet-5")
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--dataset", default="crmarenapro")
    a = ap.parse_args()
    per_arm = defaultdict(list)
    for res in a.dab.glob(f"query_{a.dataset}*/query*/logs/data_agent/*_{a.model}_r{a.run}/claude_result.json"):
        run = res.parent
        arm = run.name[0]
        cr = json.loads(res.read_text())
        u = cr["usage"]
        calls = [json.loads(l) for l in (run / "tool_calls.jsonl").read_text().splitlines()] \
            if (run / "tool_calls.jsonl").exists() else []
        kinds = Counter(kind(c) for c in calls)
        # context cost of results: a result of n chars is re-read on every later turn
        weighted = sum(c["result_chars"] * (len(calls) - i) for i, c in enumerate(calls))
        per_arm[arm].append({
            "tokens": u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"]
                      + u["output_tokens"],
            "output": u["output_tokens"], "turns": cr["num_turns"], "calls": len(calls),
            "result_chars": sum(c["result_chars"] for c in calls), "weighted_chars": weighted,
            "first_turn_ctx": None, **{k: kinds.get(k, 0) for k in ("explore", "fetch", "python", "error", "answer")},
        })
    cols = ["tokens", "output", "turns", "calls", "explore", "fetch", "python", "error", "result_chars",
            "weighted_chars"]
    print(f"{'arm':4s}{'runs':>5s}" + "".join(f"{c:>15s}" for c in cols))
    for arm, rs in sorted(per_arm.items()):
        print(f"{arm:4s}{len(rs):5d}" + "".join(f"{statistics.mean(r[c] for r in rs):15.0f}" for c in cols))
    print("(means per run)")


if __name__ == "__main__":
    main()
