"""Summarize freshness passes as Markdown (FRESHNESS.md §6).

Per arm and question kind: pass@1 (per-question averaging, with a question-clustered bootstrap
interval), the share of stale and other wrong answers, the share of wrong answers that disclose
staleness, wall-capped episodes, and median tokens, turns and seconds. For the passes: the
commit-to-readable lag of bank_live (replayed on the clock, not the end-of-pass flush), nightly
load convergence, and any question whose accounts were written during its episode.

Tokens follow score.py: input (including cache reads and writes) plus output, from Claude Code's
result. A wall-capped episode has no result, so it has no token count and is left out of the
token medians (and counted separately).

Usage: python analyze_fresh.py <dab-root> <label> [<label> ...]   (labels are runs/ subdirectories)
"""
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from fresh_common import ARMS, HERE, QUESTIONS, RUNS
from fresh_check import discloses  # after fresh_common, which puts dab/ on the path
import timeline as tl

ARM_ORDER = ["D", "UC", "UP"]
KINDS = ["changed", "control", "portfolio"]
def scope(q):
    # more than one source system: where consolidated state should matter beyond freshness
    return "cross-system" if len(q["systems"]) > 1 else "single-system"


def read_jsonl(p):
    return [json.loads(x) for x in open(p)] if p.exists() else []


def usage(dab, arm, model, q, k):
    f = dab / f"query_{ARMS[arm][0]}" / f"query{q}" / "logs" / "data_agent" / f"{arm}_cc-{model}_r{k}" / "claude_result.json"
    if not f.exists():
        return None
    r = json.loads(f.read_text())
    fa = f.parent / "final_agent.json"
    secs = json.loads(fa.read_text()).get("duration") if fa.exists() else None
    u = r.get("usage") or {}
    inp = (u.get("input_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
    return {"tokens": inp + (u.get("output_tokens") or 0), "turns": r.get("num_turns"), "cost": r.get("total_cost_usd"),
            "seconds": secs}


def pass_at_1(eps):
    by_q = defaultdict(list)
    for e in eps:
        by_q[e["q"]].append(e["verdict"] == "correct")
    return statistics.mean(statistics.mean(v) for v in by_q.values()) if by_q else float("nan")


def bootstrap(eps, n=2000, seed=0):
    by_q = defaultdict(list)
    for e in eps:
        by_q[e["q"]].append(e)
    keys, rnd, vals = sorted(by_q), random.Random(seed), []
    for _ in range(n):
        vals.append(pass_at_1([e for k in (rnd.choice(keys) for _ in keys) for e in by_q[k]]))
    vals.sort()
    return vals[int(0.025 * n)], vals[int(0.975 * n) - 1]


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def main():
    dab, labels = Path(sys.argv[1]).resolve(), sys.argv[2:]
    qs = {q["id"]: q for q in json.loads(QUESTIONS.read_text())}
    eps, lags, loads, quiet, stress = [], [], [], [], []
    for label in labels:
        d = RUNS / label
        model, k = label.rsplit("_p", 1)
        model = model[3:] if model.startswith("cc-") else None
        speed = json.loads((d / "pass.json").read_text())["speed"] if (d / "pass.json").exists() else 1.0
        events = read_jsonl(d / "events.jsonl")
        commits = defaultdict(list)
        for e in events:
            commits[e["account_id"]].append(e["commit"])
        clocked = {e["ref"] for e in events if not e["flush"]}
        lag_rows = read_jsonl(d / "lag.jsonl")
        lags += [x["lag_s"] for x in lag_rows if x["ref"] in clocked]
        flushed = [e for e in events if e["flush"]]
        if len(flushed) > 1:
            span = flushed[-1]["commit"] - flushed[0]["commit"]
            fl = [x["lag_s"] for x in lag_rows if x["ref"] not in clocked]
            seen = [x["seen"] for x in lag_rows if x["ref"] not in clocked]
            drain = (max(seen) - flushed[0]["commit"]) if seen else float("nan")
            stress.append((label, len(flushed), len(flushed) / span if span else float("nan"), fl,
                           len(flushed) / drain if drain else float("nan")))
        loads += [x for x in read_jsonl(d / "loads.jsonl") if "day" in x]
        for e in read_jsonl(d / "episodes.jsonl"):
            e["label"] = label
            e["scope"] = scope(qs[e["q"]])
            e["discloses"] = discloses(e.get("answer"))  # rescored, so a regex fix applies to every pass
            e["usage"] = usage(dab, e["arm"], model, e["q"], int(k)) if model else None
            eps.append(e)
            q = qs[e["q"]]
            if q["accounts"]:
                lo = e["start"] - tl.MARGIN_S / speed
                hit = [c for a in q["accounts"] for c in commits[a] if lo <= c <= e["end"]]
                if hit and e["arm"] == "D":
                    quiet.append((label, e["q"], len(hit)))

    print(f"Passes: {', '.join(labels)}; {len(eps)} episodes.\n")
    groups = [("all", lambda e: True)] + [(k, lambda e, k=k: e["kind"] == k) for k in KINDS]
    groups += [(f"{sc}{' changed' if ch else ''}", lambda e, sc=sc, ch=ch: e["scope"] == sc and (not ch or e["kind"] != "control"))
               for sc in ("cross-system", "single-system") for ch in (False, True)]
    for kind, keep in groups:
        sel = [e for e in eps if keep(e)]
        if not sel:
            continue
        print(f"### {kind} ({len({e['q'] for e in sel})} questions)\n")
        print("| Arm | Runs | pass@1 | 95% CI | Stale | Other wrong | Wrong that disclose staleness | Wall-capped | "
              "Median tokens | Median turns | Median s |")
        print("|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|")
        for arm in ARM_ORDER:
            rs = [e for e in sel if e["arm"] == arm]
            if not rs:
                continue
            lo, hi = bootstrap(rs)
            wrong = [e for e in rs if e["verdict"] != "correct"]
            us = [e["usage"] for e in rs if e["usage"]]
            med = lambda f: f"{statistics.median(u[f] for u in us):,.0f}" if us else "-"  # noqa: E731
            print(f"| {arm} | {len(rs)} | {pass_at_1(rs):.3f} | {lo:.2f}–{hi:.2f} | "
                  f"{sum(e['verdict'] == 'stale' for e in rs)} | {sum(e['verdict'] == 'other' for e in rs)} | "
                  f"{sum(e['discloses'] for e in wrong)}/{len(wrong)} | {sum(e.get('wall_cap', False) for e in rs)} | "
                  f"{med('tokens')} | {med('turns')} | {med('seconds')} |")
        print()

    print("### Commit-to-readable lag of bank_live (seconds)\n")
    if lags:
        print(f"{len(lags)} transactions: p50 {pct(lags, 50):.3f}, p95 {pct(lags, 95):.3f}, p99 {pct(lags, 99):.3f}, "
              f"max {max(lags):.3f}; over the quiet margin of {tl.MARGIN_S} s: {sum(x > tl.MARGIN_S for x in lags)}\n")
    print("### Stress: the end-of-pass replay at full speed (no agents)\n")
    for label, n, rate, fl, drained in stress:
        if fl:
            print(f"{label}: {n} transactions written at {rate:.0f} per second, readable at {drained:.0f} per second; lag p50 {pct(fl, 50):.3f}, p95 {pct(fl, 95):.3f}, "
                  f"p99 {pct(fl, 99):.3f}, max {max(fl):.3f} s")
    print()
    print("### Nightly loads\n")
    late = [x for x in loads if x["converged_after_s"] is None]
    print(f"{len(loads)} loads; the view had caught up before every load: {not late}\n")
    print("### Quiet windows\n")
    print("Questions whose accounts were written during the episode: " + (", ".join(f"{l} q{q} ({n})" for l, q, n in quiet)
                                                                          or "none") + "\n")
    print("### Episodes with no submitted answer\n")
    print("Ended without calling return_answer (scored as other, per the DAB protocol): "
          + (", ".join(f"{e['label']} q{e['q']} {e['arm']}" for e in eps if not e.get("answer")) or "none") + "\n")
    print("### Per question (correct / stale / other, over passes)\n")
    print("| Q | Time | Kind | Scope | Template | " + " | ".join(ARM_ORDER) + " |")
    print("|---|---|---|---|---|" + "---|" * len(ARM_ORDER))
    for qid in sorted(qs):
        rs = [e for e in eps if e["q"] == qid]
        if not rs:
            continue
        cells = []
        for arm in ARM_ORDER:
            a = [e["verdict"] for e in rs if e["arm"] == arm]
            cells.append(f"{a.count('correct')}/{a.count('stale')}/{a.count('other')}" if a else "-")
        q = qs[qid]
        print(f"| {qid} | {q['sim_time']} | {q['kind']} | {scope(q)} | {q['template']} | " + " | ".join(cells) + " |")


if __name__ == "__main__":
    main()
