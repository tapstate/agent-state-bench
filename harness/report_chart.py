"""Accuracy vs. cost per correct answer, raw sources (A) -> Tapstate consolidated state (C), one panel per model.

Usage: python harness/report_chart.py results.csv out.svg
"""
import collections
import csv
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import score as S  # noqa: E402

A_COLOR, C_COLOR = "#eb6834", "#2a78d6"          # categorical slots 2 and 1 of the reference palette
INK, MUTED, GRID, SURFACE = "#1f1f1e", "#6b6b68", "#e6e6e3", "#fcfcfb"
NAMES = {"cve": "cve*", "music_brainz_20k": "music", "bookreview": "bookreview", "yelp": "yelp",
         "googlelocal": "googlelocal", "agnews": "agnews", "crmarenapro": "crm"}
MODELS = [("haiku", "Haiku 4.5"), ("sonnet", "Sonnet 5"), ("opus", "Opus 5")]
# label offsets (points) where the default, up and to the left of the C point, would collide
OFFSET = {("sonnet", "bookreview"): (-8, 6), ("sonnet", "music_brainz_20k"): (4, 8),
          ("sonnet", "yelp"): (-8, -12), ("sonnet", "crmarenapro"): (8, 4),
          ("opus", "cve"): (6, 7), ("opus", "bookreview"): (-10, 6), ("opus", "yelp"): (-9, -2), ("opus", "music_brainz_20k"): (-9, -3),
          ("opus", "googlelocal"): (-9, -3),
          ("opus", "crmarenapro"): (8, -3), ("haiku", "cve"): (8, 4), ("haiku", "crmarenapro"): (8, -3)}


def load(path):
    g = collections.defaultdict(list)
    for r in csv.DictReader(open(path)):
        if r["dataset"] not in NAMES or r["arm"] not in ("A", "C"):
            continue
        r["valid"] = r["valid"] == "True"
        r["cost_usd"] = float(r["cost_usd"])
        m = next(k for k, _ in MODELS if k in r["model"])
        g[(r["dataset"], m, r["arm"])].append(r)
    return g


def main():
    g = load(sys.argv[1])
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "text.color": INK,
                         "axes.labelcolor": MUTED, "xtick.color": MUTED, "ytick.color": MUTED})
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.2), sharey=True, sharex=True, facecolor=SURFACE)
    for ax, (m, title) in zip(axes, MODELS):
        ax.set_facecolor(SURFACE)
        for ds, name in NAMES.items():
            a, c = g.get((ds, m, "A")), g.get((ds, m, "C"))
            if not (a and c):
                continue
            pa, pc = S.pass_at_1(a), S.pass_at_1(c)
            ca, cc = S.cost_per_correct(a), S.cost_per_correct(c)
            ca, cc = min(ca, 40), min(cc, 40)  # an arm with no correct answer sits at the right edge
            ax.annotate("", xy=(cc, pc), xytext=(ca, pa),
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2, shrinkA=5, shrinkB=5))
            ax.scatter([ca], [pa], s=46, color=A_COLOR, edgecolor=SURFACE, linewidth=2, zorder=3)
            ax.scatter([cc], [pc], s=46, color=C_COLOR, edgecolor=SURFACE, linewidth=2, zorder=4)
            dx, dy = OFFSET.get((m, ds), (-6, 5))
            ax.annotate(name, (cc, pc), xytext=(dx, dy), textcoords="offset points",
                        ha="left" if dx > 0 else "right", fontsize=8, color=INK)
        ax.set_xscale("log")
        ax.set_xlim(0.01, 40)
        ax.set_xticks([0.01, 0.1, 1, 10])
        ax.set_xticklabels(["$0.01", "$0.10", "$1", "$10"])
        ax.set_ylim(-0.03, 1.08)
        ax.set_title(title, loc="left", fontsize=10, color=INK)
        ax.grid(True, color=GRID, lw=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color(GRID)
        ax.set_xlabel("cost per correct answer (log scale)")
    axes[0].set_ylabel("share of questions answered correctly")
    handles = [plt.Line2D([], [], marker="o", ls="", color=A_COLOR, markersize=7, label="A: raw sources"),
               plt.Line2D([], [], marker="o", ls="", color=C_COLOR, markersize=7, label="C: Tapstate consolidated")]
    fig.legend(handles=handles, loc="upper right", frameon=False, ncol=2)
    fig.text(0.01, 0.01, "Up and to the left is better. * cve is an upper bound (its key decoder mirrors the "
             "dataset generator). 5 runs per question; costs are API-equivalent.", fontsize=7.5, color=MUTED)
    fig.tight_layout(rect=(0, 0.04, 1, 0.94))
    fig.savefig(sys.argv[2], facecolor=SURFACE)
    fig.savefig(str(Path(sys.argv[2]).with_suffix(".png")), dpi=160, facecolor=SURFACE)


if __name__ == "__main__":
    main()
