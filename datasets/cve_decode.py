"""Resolve DAB cve's scrambled CVE join keys to canonical ids ("CVE-2023-12345").

UPPER-BOUND TOOL: this inverts the dataset's own generator (query_cve/manual_querycode/
corrupt.py — its prefix / suffix / wrapper / infix token tables and 32 key encodings). No
customer-shippable rule could do this; Arm C on cve therefore measures the value of perfectly
resolved state, not what a product pipeline would reach.

Decoding a key: strip one known prefix and suffix, one known wrapper pair, remove one known
infix token, then parse one of the 32 encodings. Several parses can fit a key; the one whose
re-encoding is consistent (valid year/number, and re-derivable where the encoding is exact)
wins. `export_tables()` writes the token tables as JSON for the pipelines' JS port.

Usage: python cve_decode.py <dab-root> --check     decode every key in the four stores, report
       python cve_decode.py <dab-root> --export F  write token tables to JSON file F
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

GEN = None  # the dataset's corrupt.py module, loaded from the DAB checkout
OCR_BACK = {"O": "0", "I": "1", "Z": "2", "S": "5", "G": "6", "B": "8"}
YEARS = range(1999, 2031)


def load_generator(dab):
    global GEN
    p = Path(dab) / "query_cve" / "manual_querycode" / "corrupt.py"
    spec = importlib.util.spec_from_file_location("cve_corrupt", p)
    GEN = importlib.util.module_from_spec(spec)
    sys.modules["cve_corrupt"] = GEN
    spec.loader.exec_module(GEN)
    global PREFIXES, SUFFIXES, WRAP_L, WRAP_R, WRAP_PAIRS, INFIXES, INFIX_LENS
    PREFIXES = set(GEN.CVE_KEY_PREFIXES)
    SUFFIXES = set(GEN.CVE_KEY_SUFFIXES)
    WRAP_PAIRS = set(GEN.CVE_KEY_WRAPPERS)
    WRAP_L = {l for l, _ in WRAP_PAIRS}
    WRAP_R = {r for _, r in WRAP_PAIRS}
    INFIXES = set(GEN.CVE_KEY_INFIXES)
    INFIX_LENS = sorted({len(t) for t in INFIXES})


def _b36(s):
    return int(s, 36)


def _ocr(s):
    return "".join(OCR_BACK.get(c, c) for c in s)


def _deinterleave(s, a_len):
    """Inverse of corrupt._interleave(a, b) where len(a) == a_len."""
    a, b, i = [], [], 0
    while i < len(s):
        if len(a) < a_len:
            a.append(s[i]); i += 1
        if i < len(s):
            b.append(s[i]); i += 1
    return "".join(a), "".join(b)


def parse_family(k):
    """All (year, num) readings of an unwrapped, de-noised key."""
    out = []

    def add(y, n):
        try:
            y, n = int(y), int(n)
        except (TypeError, ValueError):
            return
        if y in YEARS and 0 < n < 10**7:
            out.append((y, n))

    m = re.fullmatch(r"(?:CVE-|cve-|CVE_|cve:)?(\d{4})(?:-|_|:|::)(\d+)", k)
    if m:
        add(m[1], m[2])
    if m := re.fullmatch(r"yr(\d+)_n(\d+)", k):
        add(int(m[1]) + 1999, int(m[2]) - 17)
    if m := re.fullmatch(r"n(\d+)_y(\d+)", k):
        add(int(m[2]) + 1900, int(m[1]) - 100000)
    if m := re.fullmatch(r"rv(\d{4})-(\d+)", k):
        add(m[1][::-1], m[2][::-1])
    if m := re.fullmatch(r"b36y([0-9a-z]+)", k):
        body = m[1]  # "n" is also a base-36 digit, so try every split point
        for i, ch in enumerate(body):
            if ch == "n" and 0 < i < len(body) - 1:
                add(_b36(body[:i]), _b36(body[i + 1:]))
    if m := re.fullmatch(r"h([0-9a-f]+)x([0-9a-f]+)", k):
        add(int(m[1], 16), int(m[2], 16))
    if m := re.fullmatch(r"ord(\d{2})\.(\d{7})", k):
        add(int(m[1]) + 2000, m[2])
    if m := re.fullmatch(r"mx(\d{2})-(\d{2})-(\d{2})-(\d+)", k):
        add(m[1] + m[3], m[2] + m[4])
    if m := re.fullmatch(r"sp(\d{2})_(\d{2})_(\d+)_(\d{2})", k):
        add(m[4] + m[2], m[1] + m[3])
    if m := re.fullmatch(r"k(\d{4})(\d{7})", k):
        add(m[1], m[2])
    if m := re.fullmatch(r"off(\d+)\.(\d+)", k):
        add(int(m[1]) - 37, int(m[2]) - 7919)
    if m := re.fullmatch(r"p(\d{2})(\d{2})\.(\d+)\.(\d+)", k):
        add(int(m[3]) * 100 + int(m[1]), m[4])
    if m := re.fullmatch(r"dot([\d.]+)", k):
        d = m[1].replace(".", "")
        if len(d) == 11:
            add(d[:4], d[4:])
    if m := re.fullmatch(r"ocr([A-Z0-9]+)", k):
        d = _ocr(m[1])
        if d.startswith("CVE") and len(d) == 14:
            add(d[3:7], d[7:])
    if m := re.fullmatch(r"ob([A-Z0-9]{4})-([A-Z0-9]+)", k):
        add(_ocr(m[1]), _ocr(m[2]))
    if m := re.fullmatch(r"i(\d{11})", k):
        y, n = _deinterleave(m[1], 4)
        add(y, n)
    if m := re.fullmatch(r"ri(\d{11})", k):
        y, n = _deinterleave(m[1], 4)
        add(y[::-1], n[::-1])
    if m := re.fullmatch(r"b36mix([0-9a-z]+)_(\d{7})", k):
        add(_b36(m[1]) + 1990, m[2][::-1])
    if m := re.fullmatch(r"hexmix([0-9a-f]+)_(\d+)", k):
        add(int(m[2]) + 1970, int(m[1], 16))
    if m := re.fullmatch(r"win([+-]\d+)/(\d+)", k):
        n3 = int(m[2]) - 1
        if n3 % 3 == 0:
            add(int(m[1]) + 2010, n3 // 3)
    if m := re.fullmatch(r"tri(\d+)-(\d+)", k):
        y3, n5 = int(m[1]) - 2, int(m[2]) - 4
        if y3 % 3 == 0 and n5 % 5 == 0:
            add(y3 // 3, n5 // 5)
    if m := re.fullmatch(r"swap(\d{3})-(\d{4})-(\d{4})", k):
        add(m[2], m[1] + m[3])
    if m := re.fullmatch(r"fold(\d{2})(\d{2})-(\d{2})(\d{5})", k):
        add(m[1] + m[3], m[4] + m[2])
    if m := re.fullmatch(r"ocrx([A-Z0-9]+)_([A-Z0-9]+)", k):
        add(int(_ocr(m[1])) - 101, int(_ocr(m[2])) - 202)
    if m := re.fullmatch(r"z([0-9a-z]+)\.(\d{4})", k):
        y = int(m[2][::-1])
        add(y, _b36(m[1]) - y)
    return out


def _cands_between(s):
    """(inner) strings left after stripping one prefix+suffix and one wrapper pair."""
    res = []
    for pl in range(8, min(len(s), 20)):
        p = s[:pl]
        if p not in PREFIXES:
            continue
        for sl in range(7, min(len(s) - pl, 20)):
            if s[-sl:] not in SUFFIXES:
                continue
            mid = s[pl:len(s) - sl]
            for ll in range(3, min(len(mid), 12)):
                wl = mid[:ll]
                if wl not in WRAP_L:
                    continue
                for rl in range(3, min(len(mid) - ll, 12)):
                    wr = mid[len(mid) - rl:]
                    if (wl, wr) in WRAP_PAIRS:
                        res.append(mid[ll:len(mid) - rl])
    return res


def decode(s):
    """Canonical CVE id for a scrambled key, or None when no reading fits."""
    if s is None:
        return None
    found = set()
    for inner in _cands_between(s):
        for L in INFIX_LENS:
            for i in range(0, len(inner) - L + 1):
                if inner[i:i + L] in INFIXES:
                    for y, n in parse_family(inner[:i] + inner[i + L:]):
                        found.add(f"CVE-{y}-{n:04d}")
    if len(found) == 1:
        return found.pop()
    if len(found) > 1:
        # keep only readings the generator could have produced for this exact key text
        exact = {c for c in found if _reencodes(c, s)}
        if len(exact) == 1:
            return exact.pop()
        return "AMBIGUOUS:" + "|".join(sorted(found))
    return None


def _reencodes(cve, s):
    """Whether some context makes corrupt.reformat_cve_id(cve, context) produce the inner key
    shape of s: checked by re-parsing only — context is unknown, so this is a weak filter."""
    y, n = cve[4:].split("-")
    return str(int(n)) in s or n in s or y in s or y[::-1] in s


def check(dab):
    import duckdb
    import sqlite3
    from collections import Counter
    qd = Path(dab) / "query_cve" / "query_dataset"
    sources = {}
    c = sqlite3.connect(qd / "vulns.db")
    sources["cves"] = [r[0] for r in c.execute("select cve_id from cves")]
    sources["cvss_metadata"] = [r[0] for r in c.execute("select cve_id from cvss_metadata")]
    d = duckdb.connect(str(qd / "cpe.duckdb"), read_only=True)
    sources["cpe_matches"] = [r[0] for r in d.execute("select cve_id from cpe_matches").fetchall()]
    kev_rows = re.findall(r"^INSERT INTO kev_entries VALUES \('((?:[^']|'')*)'.*?(CVE-\d{4}-\d+)'\);$",
                          (qd / "kev.sql").read_text(), re.M)
    sources["kev_entries"] = [k.replace("''", "'") for k, _ in kev_rows]
    # independent truth: a KEV row's notes end with the NVD URL of its CVE
    truth = [(k.replace("''", "'"), t) for k, t in kev_rows]
    agree = sum(decode(k) == t for k, t in truth)
    print(f"kev decode vs NVD URL in notes: {agree}/{len(truth)} agree")
    from pymongo import MongoClient
    m = MongoClient("mongodb://127.0.0.1:27017/?directConnection=true")["cve_descriptions"]
    sources["cve_documents"] = [doc["cve"] for doc in m.cve_documents.find({}, {"cve": 1})]
    ids = {}
    for name, keys in sources.items():
        stats = Counter()
        decoded = set()
        for k in keys:
            r = decode(k)
            if r is None:
                stats["none"] += 1
            elif r.startswith("AMBIGUOUS"):
                stats["ambiguous"] += 1
            else:
                stats["ok"] += 1
                decoded.add(r)
        ids[name] = decoded
        print(f"{name:15s} keys={len(keys):7d} ok={stats['ok']:7d} ambiguous={stats['ambiguous']:5d} "
              f"undecoded={stats['none']:5d} distinct={len(decoded)}")
    base = ids["cves"]
    for name in ("cvss_metadata", "cpe_matches", "kev_entries", "cve_documents"):
        print(f"{name:15s} ids found in cves: {len(ids[name] & base) / max(1, len(ids[name])):.1%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dab")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--export")
    a = ap.parse_args()
    load_generator(a.dab)
    if a.check:
        check(a.dab)
    if a.export:
        Path(a.export).write_text(json.dumps({
            "prefixes": sorted(PREFIXES), "suffixes": sorted(SUFFIXES),
            "wrappers": sorted([list(p) for p in WRAP_PAIRS]), "infixes": sorted(INFIXES)}))


if __name__ == "__main__":
    main()
