"""Grade music_er.py's clustering against the true clusters of the public MusicBrainz 20K dataset.

DAB's music_brainz_20k tracks are that dataset's records (same record ids, sources and source ids),
so its cluster id CID is the truth. The truth is used only here, to grade the matcher after it
was built; music_er.py never reads it.

Reports pairwise precision / recall / F1 (over all pairs of records placed in one cluster), the
cluster count, and the same figures for the songs the benchmark questions name, which the matcher
was checked on while it was written.

Usage: python eval_music_er.py <musicbrainz-20-A01.csv.dapo>
"""
import csv
import sys
from collections import Counter, defaultdict
from itertools import combinations

import psycopg2

DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=musicsrc"


def pairs(clusters):
    return {tuple(sorted(p)) for members in clusters.values() for p in combinations(members, 2)}


def prf(pred, true):
    tp = len(pred & true)
    p = tp / len(pred) if pred else 1.0
    r = tp / len(true) if true else 1.0
    return p, r, 2 * p * r / (p + r) if p + r else 0.0


def main():
    truth, meta = {}, {}
    with open(sys.argv[1], newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            truth[int(row["TID"])] = int(row["CID"])
            meta[int(row["TID"])] = (int(row["SourceID"]), row["id"])
    cur = psycopg2.connect(DSN).cursor()
    cur.execute("select track_id, source_id, source_track_id from tracks")
    dab = {t: (s, sid) for t, s, sid in cur.fetchall()}
    mismatch = [t for t in dab if meta.get(t) != dab[t]]
    print(f"records: dataset {len(truth)}, DAB {len(dab)}, id/source mismatches {len(mismatch)}")
    cur.execute("select track_id, song_id from song_xwalk")
    pred = dict(cur.fetchall())
    assert set(pred) == set(truth), "record sets differ"

    def clusters(assign, keep=lambda t: True):
        c = defaultdict(list)
        for t, k in assign.items():
            if keep(t):
                c[k].append(t)
        return c

    P, T = pairs(clusters(pred)), pairs(clusters(truth))
    p, r, f1 = prf(P, T)
    print(f"all songs: true clusters {len(set(truth.values()))}, predicted {len(set(pred.values()))}")
    print(f"  pairwise precision {p:.4f}  recall {r:.4f}  F1 {f1:.4f}  (true pairs {len(T)}, predicted {len(P)})")
    exact = sum(1 for c in clusters(truth).values() if len({pred[t] for t in c}) == 1
                and len(clusters(pred)[pred[c[0]]]) == len(c))
    print(f"  true clusters reproduced exactly: {exact} / {len(set(truth.values()))}")

    # the songs the three questions name, found by their true clusters
    cur.execute("select track_id, title, artist from tracks")
    rows = cur.fetchall()
    for label, needle in (("q1 Get Me Bodied", "bodied"), ("q2 Street Hype", "street hype")):
        cids = Counter(truth[t] for t, title, _ in rows if needle in (title or "").lower().replace("getme", "get me"))
        for cid, n in cids.most_common(1):
            members = [t for t in truth if truth[t] == cid]
            got = Counter(pred[t] for t in members)
            extra = sum(1 for t in pred if pred[t] in got and truth[t] != cid)
            print(f"{label}: true cluster of {len(members)} records -> {len(got)} predicted cluster(s) "
                  f"{dict(got)}, foreign records merged in: {extra}")
    others = lambda t: True
    return 0


if __name__ == "__main__":
    sys.exit(main())
