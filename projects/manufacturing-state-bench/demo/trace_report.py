"""Turn two sales-agent runs (setup A and setup C, from sales_agent.py) into one self-contained HTML
page for the demo: the answer and whether it is correct, the headline numbers side by side, tokens
over the run as a chart, and every call the agent made, with its query, the system it went to, how
much came back, and the tokens spent before it.

Usage: python trace_report.py runs/A_<ts> runs/C_<ts> [-o trace_report.html] [--lang zh]
--lang zh writes the page in Simplified Chinese (i18n.py); queries and answers stay as recorded.
"""
import argparse
import html
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import i18n  # noqa: E402
from i18n import t  # noqa: E402

COLORS = {"A": "#c2410c", "C": "#0f766e"}
LABELS = {"A": "Six live systems", "C": "Tapstate"}


def load(d):
    s = json.loads((Path(d) / "summary.json").read_text())
    s["dir"] = str(d)
    return s


def esc(x):
    return html.escape(str(x if x is not None else ""))


def chart(runs, w=640, h=220, pad=44):
    """Cumulative tokens by tool call, one step line per setup."""
    pts = {}
    for r in runs:
        ys = [c["tokens_before"] for c in r["calls"]] + [r["tokens"]["total"]]
        pts[r["setup"]] = ys
    top = max(max(v) for v in pts.values()) or 1
    n = max(len(v) for v in pts.values()) - 1 or 1
    X = lambda i: pad + i * (w - pad - 12) / n  # noqa: E731
    Y = lambda v: h - 26 - v * (h - 46) / top   # noqa: E731
    out = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="{t("Tokens spent over the run")}">']
    for f in (0, .5, 1):
        v = top * f
        out.append(f'<line x1="{pad}" x2="{w - 12}" y1="{Y(v):.1f}" y2="{Y(v):.1f}" class="grid"/>'
                   f'<text x="{pad - 6}" y="{Y(v) + 4:.1f}" text-anchor="end" class="ax">{v / 1000:.0f}k</text>')
    out.append(f'<text x="{(w + pad) / 2}" y="{h - 4}" text-anchor="middle" class="ax">{t("tool calls, in order")}</text>')
    for setup, ys in pts.items():
        d = "M" + " L".join(f"{X(i):.1f},{Y(y):.1f}" for i, y in enumerate(ys))
        out.append(f'<path d="{d}" fill="none" stroke="{COLORS[setup]}" stroke-width="2.5"/>')
        i = len(ys) - 1
        out.append(f'<circle cx="{X(i):.1f}" cy="{Y(ys[i]):.1f}" r="4" fill="{COLORS[setup]}"/>'
                   f'<text x="{X(i) - 6:.1f}" y="{Y(ys[i]) - 9:.1f}" text-anchor="end" class="lbl" '
                   f'fill="{COLORS[setup]}">{t(LABELS[setup])}{"：" if i18n.LANG == "zh" else ": "}{ys[i] / 1000:.0f}k</text>')
    out.append("</svg>")
    return "".join(out)


def card(r):
    tk = r["tokens"]
    ok = f'<span class="ok">{t("correct")}</span>' if r.get("correct") else f'<span class="bad">{t("not correct")}</span>'
    systems = ("，" if i18n.LANG == "zh" else ", ").join(f"{esc(t(k))} {v}" for k, v in r["queries_by_system"].items())
    tr = i18n.answer_translation(r.get("answer"))
    cost = f"${r['cost_usd']:.2f}" if r.get("cost_usd") is not None else "n/a"
    return f"""<section class="card" style="--c:{COLORS[r['setup']]}">
<h2>{t(LABELS[r['setup']])}</h2>
<div class="big">{tk['total'] / 1000:.0f}k <small>{t('tokens_unit')}</small></div>
<dl>
<dt>{t('Round trips to the model')}</dt><dd>{r['round_trips']}</dd>
<dt>{t('Queries issued')}</dt><dd>{r['queries']}</dd>
<dt>{t('By system')}</dt><dd>{systems or '-'}</dd>
<dt>{t('Failed calls')}</dt><dd>{r['errors']}</dd>
<dt>{t('Cost (API-equivalent)')}</dt><dd>{cost}</dd>
<dt>{t('Wall time')}</dt><dd>{r['duration_s']} s</dd>
<dt>{t('Answer')}</dt><dd>{ok}</dd>
</dl>
<p class="ans">{esc(r.get('answer'))}</p>{f'<p class="ans">（{t("translation")}）{esc(tr)}</p>' if tr else ''}
</section>"""


def calls(r):
    rows = []
    for c in r["calls"]:
        q = esc(c["query"])
        cls = ' class="err"' if c.get("error") else ""
        rows.append(f"<tr{cls}><td>{c['n']}</td><td>{c['round_trip']}</td><td>{esc(c['tool'])}</td>"
                    f"<td>{esc(t(c['system']) if c['tool'] in ('query_db', 'list_db') else '')}</td>"
                    f"<td><details><summary>{esc(' '.join(str(c['query']).split())[:80])}</summary><pre>{q}</pre></details></td>"
                    f"<td class='n'>{c.get('result_chars', 0):,}</td><td class='n'>{c['tokens_before'] / 1000:.1f}k</td></tr>")
    head = f"{t(LABELS[r['setup']])}：{t('every call')}" if i18n.LANG == "zh" else f"{LABELS[r['setup']]}: every call"
    return f"""<section><h3 style="color:{COLORS[r['setup']]}">{head}</h3>
<div class="scroll"><table><thead><tr><th>#</th><th>{t('Round trip')}</th><th>{t('Tool')}</th><th>{t('System')}</th>
<th>{t('Query or code')}</th><th class='n'>{t('Chars back')}</th><th class='n'>{t('Tokens so far')}</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div></section>"""


def page(a, c):
    ratio = a["tokens"]["total"] / max(c["tokens"]["total"], 1)
    zh = i18n.LANG == "zh"
    if zh:
        hl = t("ratio").format(ratio=ratio, ra=a["round_trips"], rc=c["round_trips"])
        foot = t("footer").format(model=esc(a.get("model")), a=esc(a["dir"]), c=esc(c["dir"]))
    else:
        hl = (f"Against six live systems the agent spent <b>{ratio:.1f}×</b> the tokens it spent against Tapstate,\n"
              f"over {a['round_trips']} round trips instead of {c['round_trips']}.")
        foot = (f"Model: {esc(a.get('model'))}. Tokens are input + cache write + cache read + output, as in the "
                f"benchmark.\nRuns: {esc(a['dir'])}, {esc(c['dir'])}.")
    font = ('"Noto Sans SC","Noto Sans CJK SC","PingFang SC","Microsoft YaHei",system-ui,sans-serif' if zh
            else "system-ui,sans-serif")
    return f"""<!doctype html><html lang="{'zh-CN' if zh else 'en'}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{'销售助理智能体运行对比' if zh else 'Sales agent trace'}</title>
{'<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Sans+SC:wght@400;700&display=block">' if zh else ''}
<style>
:root{{--bg:#fafaf9;--fg:#1c1917;--mut:#78716c;--line:#e7e5e4;--card:#fff}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1c1917;--fg:#f5f5f4;--mut:#a8a29e;--line:#44403c;--card:#292524}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 {font}}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} .q{{color:var(--mut);margin:0 0 20px}}
.grid2{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px}}
.card{{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--c);border-radius:8px;padding:16px}}
.card h2{{margin:0;font-size:16px;color:var(--c)}} .big{{font-size:40px;font-weight:700}} .big small{{font-size:15px;color:var(--mut)}}
dl{{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:8px 0}} dt{{color:var(--mut)}} dd{{margin:0}}
.ok{{color:#15803d;font-weight:600}} .bad{{color:#b91c1c;font-weight:600}} .ans{{font-size:13px;color:var(--mut)}}
.hl{{font-size:18px;margin:20px 0}} svg{{width:100%;max-width:700px}} .grid{{stroke:var(--line)}}
.ax{{fill:var(--mut);font-size:11px}} .lbl{{font-size:12px;font-weight:600}}
.scroll{{overflow-x:auto}} table{{border-collapse:collapse;width:100%;font-size:13px}}
th,td{{text-align:left;padding:4px 8px;border-bottom:1px solid var(--line);vertical-align:top}} .n{{text-align:right}}
tr.err td{{color:#b91c1c}} pre{{white-space:pre-wrap;font-size:12px;margin:4px 0}} summary{{cursor:pointer}}
</style></head><body><main>
<h1>{t('Sales agent: what it took to answer one customer')}</h1>
<p class="q">{esc(i18n.question(a.get('question')))}</p>
<div class="grid2">{card(a)}{card(c)}</div>
<p class="hl">{hl}</p>
{chart([a, c])}
{calls(a)}
{calls(c)}
<p class="q">{foot}</p>
</main></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a")
    ap.add_argument("c")
    ap.add_argument("-o", "--out", default="trace_report.html")
    ap.add_argument("--lang", choices=["en", "zh"], default="en", help="page language (zh: Simplified Chinese)")
    x = ap.parse_args()
    i18n.set_lang(x.lang)
    a, c = load(x.a), load(x.c)
    Path(x.out).write_text(page(a, c))
    print(f"wrote {x.out}")


if __name__ == "__main__":
    main()
