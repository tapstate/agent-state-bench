"""Score runs written by run_arms.py: accuracy, tokens and cost per correct answer, per arm x model.

Per run: the official DAB validator (query's validate.py via common_scaffold/validate), a
stricter exact-set check against ground_truth.csv, and token usage summed over every LLM call
in llm_calls.jsonl. Cost uses list prices (USD per million tokens) recorded below with the
date they were read; failed runs count toward cost, so cost per correct answer cannot be won
by being cheap and wrong.

Usage: python score.py <dab-root> [--csv out.csv]
"""
import argparse
import csv
import json
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

# List prices read 2026-09-23 (Claude API reference): input, output, cache read.
PRICES = {
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-sonnet-5": (2.00, 10.00, 0.20),
    "claude-haiku-4-5": (1.00, 5.00, 0.10),
}
ROOT_RE = re.compile(r"^(?P<arm>[A-Z][0-9]?)_(?P<model>.+)_r(?P<run>\d+)$")  # model "cc-<id>" = Claude Code agent


def norm(s):
    return re.sub(r"[^a-z0-9.\-]+", " ", s.lower()).split()


def strict_ok(answer, gt_lines):
    """Exact set match: the answer names every ground-truth value and nothing else."""
    want = {t for line in gt_lines for t in norm(line)}
    got = set(norm(answer))
    return bool(want) and got == want


def usage(llm_log):
    inp = out = cached = calls = 0
    for line in llm_log.read_text().splitlines():
        rec = json.loads(line)
        u = (rec.get("response") or {}).get("usage") or {}
        calls += 1
        inp += u.get("prompt_tokens") or 0
        out += u.get("completion_tokens") or 0
        cached += ((u.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0
    return inp, out, cached, calls


def pass_at_1(rs):
    """Mean over questions of each question's pass rate (DAB's per-question averaging)."""
    by_q = defaultdict(list)
    for r in rs:
        by_q[r["query"]].append(r["valid"])
    return statistics.mean(statistics.mean(v) for v in by_q.values())


def cost_per_correct(rs):
    """Total spend, failed runs included, over the number of correct runs."""
    correct = sum(r["valid"] for r in rs)
    return sum(r["cost_usd"] for r in rs) / correct if correct else float("inf")


def bootstrap_ci(rs, stat, n=10000, seed=0, level=0.95):
    """Percentile interval of `stat` under a question-clustered bootstrap.

    Questions, not runs, are resampled with replacement (each keeps all of its runs): runs of
    one question are correlated, so resampling runs alone would understate the uncertainty.
    """
    by_q = defaultdict(list)
    for r in rs:
        by_q[r["query"]].append(r)
    qs = sorted(by_q)
    rng = random.Random(seed)
    vals = []
    for i in range(n):
        sample = []
        for j, q in enumerate(rng.choices(qs, k=len(qs))):
            # relabel so a question drawn twice counts as two questions
            sample += [dict(r, query=f"{q}#{j}") for r in by_q[q]]
        vals.append(stat(sample))
    vals.sort()
    lo, hi = (1 - level) / 2, 1 - (1 - level) / 2
    return vals[int(lo * (n - 1))], vals[int(hi * (n - 1))]


def summarize(rs, n_boot=2000):
    p1 = pass_at_1(rs)
    p1_lo, p1_hi = bootstrap_ci(rs, pass_at_1, n=n_boot)
    c = cost_per_correct(rs)
    c_lo, c_hi = bootstrap_ci(rs, cost_per_correct, n=n_boot)
    return {
        "runs": len(rs), "pass@1": p1, "pass@1_ci": (p1_lo, p1_hi),
        "strict": statistics.mean(r["strict"] for r in rs),
        "med_tokens": int(statistics.median(r["input_tokens"] + r["output_tokens"] for r in rs)),
        "med_calls": statistics.median(r["llm_calls"] for r in rs),
        "med_s": statistics.median(r["duration_s"] for r in rs),
        "total_usd": sum(r["cost_usd"] for r in rs),
        "usd_per_correct": c, "usd_per_correct_ci": (c_lo, c_hi),
    }


def print_summary(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["dataset"], r["model"], r["arm"])].append(r)
    hdr = ("dataset", "model", "arm", "runs", "pass@1", "95% CI", "med tokens", "med calls", "med s",
           "total $", "$ / correct", "95% CI")
    print("  ".join(f"{h:>12}" for h in hdr))
    for (dataset, model, arm), rs in sorted(groups.items()):
        s = summarize(rs)
        cols = (dataset, model, arm, s["runs"], f"{s['pass@1']:.3f}",
                "{:.2f}-{:.2f}".format(*s["pass@1_ci"]), s["med_tokens"], s["med_calls"], s["med_s"],
                f"{s['total_usd']:.2f}", f"{s['usd_per_correct']:.3f}",
                "{:.3f}-{:.3f}".format(*s["usd_per_correct_ci"]))
        print("  ".join(f"{str(c):>12}" for c in cols))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab", type=Path)
    ap.add_argument("--csv", type=Path)
    a = ap.parse_args()
    sys.path.insert(0, str(a.dab))
    from common_scaffold.validate.validate import validate

    rows = []
    for final in sorted(a.dab.glob("query_*/query*/logs/data_agent/*/final_agent.json")):
        run_dir = final.parent
        m = ROOT_RE.match(run_dir.name)
        if not m:
            continue
        qdir = run_dir.parents[2]
        rec = json.loads(final.read_text())
        answer = rec.get("final_result") or ""
        v = validate(qdir, answer, rec.get("terminate_reason"))
        gt = [l.strip() for l in (qdir / "ground_truth.csv").read_text().splitlines() if l.strip()]
        if (run_dir / "claude_result.json").exists():
            # Claude Code run: its own usage totals; cost is Claude Code's API-equivalent estimate
            cr = json.loads((run_dir / "claude_result.json").read_text())
            u = cr.get("usage") or {}
            cached = u.get("cache_read_input_tokens") or 0
            inp = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + cached
            out = u.get("output_tokens") or 0
            calls = cr.get("num_turns") or 0
            cost = cr.get("total_cost_usd") or 0.0
        else:
            inp, out, cached, calls = usage(run_dir / "llm_calls.jsonl")
            pin, pout, pcache = PRICES.get(m["model"], (float("nan"),) * 3)
            cost = ((inp - cached) * pin + cached * pcache + out * pout) / 1e6
        tools = json.loads((run_dir / "final_agent.json").read_text()).get("llm_call_count", calls)
        rows.append({
            "dataset": re.sub(r"_consolidated(_v[0-9]+)?$", "", qdir.parent.name[len("query_"):]),
            "arm": m["arm"], "model": m["model"], "run": int(m["run"]), "query": qdir.name,
            "valid": bool(v["is_valid"]), "strict": strict_ok(answer, gt),
            "input_tokens": inp, "output_tokens": out, "cached_tokens": cached,
            "llm_calls": tools, "duration_s": round(rec.get("duration") or 0, 1),
            "cost_usd": round(cost, 5), "terminate": rec.get("terminate_reason") or "",
            "answer": answer.strip()[:200],
        })

    if a.csv:
        with a.csv.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    print_summary(rows)


if __name__ == "__main__":
    main()
