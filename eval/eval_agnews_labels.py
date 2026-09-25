"""Grade the ingest-time article classification against the public AG News labels.

DAB's agnews articles are the AG News corpus (train + test, 127,600 articles). Its published
labels (0 World, 1 Sports, 2 Business, 3 Sci/Tech) are used only here, to grade
classify_agnews.py after the fact; the classifier never reads them. Articles are matched on
title and description text.

Usage: python eval_agnews_labels.py <categories.tsv>
"""
import sys
from collections import Counter

import pandas as pd
import psycopg2
from huggingface_hub import hf_hub_download

LABELS = {0: "W", 1: "S", 2: "B", 3: "T"}
DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=agnewssrc"


def norm(s):
    return " ".join(str(s or "").split()).replace("\\", "")


def main():
    frames = [pd.read_parquet(hf_hub_download("fancyzhx/ag_news", f"data/{s}-00000-of-00001.parquet",
                                              repo_type="dataset")) for s in ("train", "test")]
    ag = pd.concat(frames)
    labels = {}
    for text, label in zip(ag["text"], ag["label"]):
        labels.setdefault(norm(text), LABELS[int(label)])
    got = dict(line.split("\t") for line in open(sys.argv[1]).read().splitlines())
    cur = psycopg2.connect(DSN).cursor()
    cur.execute("select article_id, title, description from articles")
    c, conf, unmatched = Counter(), Counter(), 0
    for aid, title, desc in cur.fetchall():
        truth = labels.get(norm(f"{title} {desc}"))
        if truth is None:
            unmatched += 1
            continue
        pred = got.get(str(aid))
        c["correct" if pred == truth else "wrong" if pred else "missing"] += 1
        conf[(truth, pred)] += 1
    n = sum(c.values())
    print(f"matched {n} articles to public labels ({unmatched} unmatched)")
    print(f"accuracy {c['correct'] / n:.1%}  wrong {c['wrong'] / n:.1%}  missing {c['missing'] / n:.1%}")
    for t in "WSBT":
        row = {p: conf[(t, p)] for p in "WSBT"}
        print(f"  true {t}: {row}")


if __name__ == "__main__":
    main()
