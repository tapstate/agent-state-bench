"""How far a generic resolver gets on DAB cve's scrambled keys, graded against the exact decoder.

The generic resolver uses only what a data engineer would try from looking at the keys: undo
common OCR confusions (O->0, I/l->1, Z->2, S->5, B->8, G->6), then look for a year (1999-2025)
followed by a 4-7 digit number, as "CVE-YYYY-NNNN" is written. It knows nothing about the
dataset generator's token tables or encodings. The exact decoder (datasets/cve_decode.py, an
inverse of that generator) provides the answers, used only for grading.

Reports, per key: resolved correctly / resolved wrongly / not resolved.

Usage: python eval_cve_generic_resolver.py
"""
import re
from collections import Counter

import psycopg2

DSN = "host=127.0.0.1 port=55432 user=postgres password=secret dbname=cvesrc"
OCR = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "Z": "2", "S": "5", "B": "8", "G": "6"})
PAT = re.compile(r"(?<!\d)(199\d|20[0-2]\d)\D{0,3}(\d{4,7})(?!\d)")


def resolve(key):
    core = re.sub(r"^[a-z]+-[a-z]{5}", "", key)          # leading "<word>-<5 letters>" wrapper
    core = re.sub(r"[a-z]+-[a-z]{4}$", "", core)          # trailing "<word>-<4 letters>" wrapper
    for text in (core, core.translate(OCR)):
        m = PAT.search(text)
        if m:
            return f"CVE-{m[1]}-{int(m[2]):04d}"
    return None


def main():
    cur = psycopg2.connect(DSN).cursor()
    out = Counter()
    for table in ("xw_cves", "xw_cpe_matches", "xw_kev_entries", "xw_cve_documents"):
        cur.execute(f"select surface_key, cve from {table}")
        c = Counter()
        for key, truth in cur.fetchall():
            got = resolve(key)
            c["correct" if got == truth else "wrong" if got else "unresolved"] += 1
        n = sum(c.values())
        print(f"{table:18s} {n:7d} keys: correct {c['correct'] / n:6.1%}  wrong {c['wrong'] / n:6.1%}  "
              f"unresolved {c['unresolved'] / n:6.1%}")
        out.update(c)
    n = sum(out.values())
    print(f"{'all':18s} {n:7d} keys: correct {out['correct'] / n:6.1%}  wrong {out['wrong'] / n:6.1%}  "
          f"unresolved {out['unresolved'] / n:6.1%}")


if __name__ == "__main__":
    main()
