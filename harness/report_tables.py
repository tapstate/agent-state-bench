"""Emit the report's markdown tables from a score.py CSV, so no number in the report is copied by hand.

Usage: python harness/report_tables.py results.csv > tables.md
"""
import collections
import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent / "datasets"))
import score as S  # noqa: E402

rows = []
for r in csv.DictReader(open(sys.argv[1])):
    r['valid'] = r['valid'] == 'True'; r['strict'] = r['strict'] == 'True'
    for k in ('input_tokens', 'output_tokens', 'cached_tokens', 'llm_calls', 'duration_s', 'cost_usd'):
        r[k] = float(r[k] or 0)
    r['model'] = r['model'].replace('cc-claude-', '').replace('-4-5-20251001', '').replace('-5', '')
    rows.append(r)
G = collections.defaultdict(list)
for r in rows: G[(r['dataset'], r['model'], r['arm'])].append(r)
cache = {}
def s(ds, m, a):
    k = (ds, m, a)
    if k not in cache: cache[k] = S.summarize(G[k], n_boot=2000) if G[k] else None
    return cache[k]
def p(x): return f"{x['pass@1']:.2f} [{x['pass@1_ci'][0]:.2f}–{x['pass@1_ci'][1]:.2f}]"
def usd(v): return "—" if v == float('inf') else (f"${v:.2f}" if v >= 1 else f"${v:.3f}")
def tok(v): return f"{v/1e6:.1f}M" if v >= 1e6 else f"{v/1e3:.0f}k"
DS = ['cve', 'music_brainz_20k', 'bookreview', 'yelp', 'googlelocal', 'agnews', 'crmarenapro']
NAME = {'cve': 'cve (upper bound)', 'music_brainz_20k': 'music', 'crmarenapro': 'crm'}
MODELS = ['haiku', 'sonnet', 'opus']

print("### Table 1 — correct answers and cost per correct answer, per dataset and model\n")
print("pass@1 with question-clustered 95% interval; A = raw, B = raw + DAB hints, C = Tapstate consolidated.\n")
print("| dataset | model | A | B | C | $/correct A → C | median tokens A → C |")
print("|---|---|---|---|---|---|---|")
for ds in DS:
    for m in MODELS:
        a, b, c = s(ds, m, 'A'), s(ds, m, 'B'), s(ds, m, 'C')
        if not (a and c): continue
        ratio = a['usd_per_correct'] / c['usd_per_correct'] if c['usd_per_correct'] not in (0, float('inf')) and a['usd_per_correct'] != float('inf') else None
        print(f"| {NAME.get(ds, ds)} | {m} | {p(a)} | {p(b) if b else '—'} | {p(c)} | "
              f"{usd(a['usd_per_correct'])} → {usd(c['usd_per_correct'])}{f' ({ratio:.1f}×)' if ratio and ratio < 10 else (f' ({ratio:.0f}×)' if ratio else '')} | "
              f"{tok(a['med_tokens'])} → {tok(c['med_tokens'])} |")
print("\n### Table 2 — a cheaper model on consolidated state vs. the frontier model on raw sources\n")
print("| dataset | Haiku on C | Opus on A | Opus on B | $/correct Haiku C vs Opus A |")
print("|---|---|---|---|---|")
for ds in DS:
    hc, oa, ob = s(ds, 'haiku', 'C'), s(ds, 'opus', 'A'), s(ds, 'opus', 'B')
    if hc and oa:
        print(f"| {NAME.get(ds, ds)} | {p(hc)} | {p(oa)} | {p(ob) if ob else '—'} | "
              f"{usd(hc['usd_per_correct'])} vs {usd(oa['usd_per_correct'])} |")

import partb_questions as Q
ct = {f"query{q['id']}": q['change'] for q in Q.QUESTIONS}
print("\n### Table 3 — Part B: correct answers per checkpoint (Sonnet, 15 questions × 3 runs)\n")
print("| checkpoint | A raw | B raw + hints | C consolidated | S stale copy |")
print("|---|---|---|---|---|")
for k in range(6):
    ds = f"crmlive_cp{k}"
    print(f"| cp{k} | " + " | ".join(p(s(ds, 'sonnet', a)) for a in 'ABCS') + " |")
agg = collections.defaultdict(lambda: [0, 0])
for r in rows:
    if r['dataset'].startswith('crmlive') and r['dataset'] != 'crmlive_cp0':
        x = agg[(ct[r['query']], r['arm'])]; x[0] += r['valid']; x[1] += 1
print("\n### Table 4 — Part B by change type (checkpoints 1–5 pooled, share correct)\n")
print("| change type | A | B | C | S |")
print("|---|---|---|---|---|")
for t in sorted({t for t, _ in agg}):
    print(f"| {t} | " + " | ".join(f"{agg[(t, a)][0] / agg[(t, a)][1]:.2f}" for a in 'ABCS') + " |")
