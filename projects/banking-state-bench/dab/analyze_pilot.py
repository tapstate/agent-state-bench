"""Summarize the bank pilot from score.py's CSV: accuracy and tokens per setup, overall and by
question category, as Markdown.

Accuracy is DAB's pass@1 (per-question averaging) with a question-clustered bootstrap interval,
using score.py's own functions. Tokens are input + output per run (input includes cached
tokens, as score.py counts them). "vs C" is the ratio of a setup's median tokens to setup C's
on the same questions.

Usage: python analyze_pilot.py <scores.csv> <harness-dir> [--model cc-claude-opus-5-5]
"""
import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(path, model):
    rows = []
    for r in csv.DictReader(open(path)):
        if r["dataset"] != "bank" or r["model"] != model:
            continue
        r["valid"] = r["valid"] == "True"
        r["strict"] = r["strict"] == "True"
        for k in ("input_tokens", "output_tokens", "cached_tokens", "llm_calls"):
            r[k] = int(float(r[k]))
        for k in ("duration_s", "cost_usd"):
            r[k] = float(r[k])
        rows.append(r)
    return rows


def tokens(r):
    return r["input_tokens"] + r["output_tokens"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("harness")
    ap.add_argument("--model", default="cc-claude-opus-5-5")
    a = ap.parse_args()
    sys.path.insert(0, a.harness)
    from score import bootstrap_ci, cost_per_correct, pass_at_1

    category = {f"query{q['id']}": q["category"] for q in json.loads((HERE / "questions.json").read_text())}
    rows = load(a.csv, a.model)
    if not rows:
        sys.exit("no bank rows for that model")
    groups = {"all questions": lambda q: True,
              **{c: (lambda q, c=c: category[q] == c) for c in dict.fromkeys(category.values())}}

    print(f"Model {a.model}; {len(rows)} scored runs.\n")
    for title, keep in groups.items():
        sel = [r for r in rows if keep(r["query"])]
        by_arm = defaultdict(list)
        for r in sel:
            by_arm[r["arm"]].append(r)
        n_q = len({r["query"] for r in sel})
        print(f"### {title} ({n_q} questions)\n")
        print("| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | "
              "API-equiv. $ per correct |")
        print("|---|---:|---:|---|---:|---:|---:|---:|---:|")
        med_c = statistics.median(tokens(r) for r in by_arm["C"]) if by_arm.get("C") else None
        for arm in sorted(by_arm):
            rs = by_arm[arm]
            lo, hi = bootstrap_ci(rs, pass_at_1, n=2000)
            med = statistics.median(tokens(r) for r in rs)
            ratio = f"{med / med_c:.1f}×" if med_c else "-"
            cpc = cost_per_correct(rs)
            print(f"| {arm} | {len(rs)} | {pass_at_1(rs):.3f} | {lo:.2f}–{hi:.2f} | {med:,.0f} | {ratio} | "
                  f"{statistics.median(r['llm_calls'] for r in rs):.0f} | "
                  f"{statistics.median(r['duration_s'] for r in rs):.0f} | "
                  f"{'-' if cpc == float('inf') else f'{cpc:.3f}'} |")
        print()

    print("### Per question (pass rate over runs; median tokens)\n")
    print("| Q | Category | " + " | ".join(f"{x} pass | {x} tokens" for x in ("A", "B", "C")) + " |")
    print("|---|---|" + "---:|---:|" * 3)
    by_q = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by_q[r["query"]][r["arm"]].append(r)
    for q in sorted(by_q, key=lambda x: int(x[5:])):
        cells = []
        for arm in ("A", "B", "C"):
            rs = by_q[q].get(arm, [])
            cells += [f"{sum(r['valid'] for r in rs)}/{len(rs)}" if rs else "-",
                      f"{statistics.median(tokens(r) for r in rs):,.0f}" if rs else "-"]
        print(f"| {q[5:]} | {category[q]} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
