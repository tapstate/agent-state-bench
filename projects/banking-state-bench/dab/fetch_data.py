"""Download the pinned NPCI tau-indian-banking files and check their hashes.

Only db.json is needed to build the datasets; tasks.json and split_tasks.json are kept for the
question topics and for provenance. Files land in dab/data/ (not committed).

Usage: python fetch_data.py
"""
import hashlib
import sys
import urllib.request

from common import DATA_DIR

REVISION = "747d97ff1e51f0f9a97374a5603d27bc438984a4"
BASE = f"https://huggingface.co/datasets/NPCI/tau-indian-banking/resolve/{REVISION}/tau2/domains/indian_banking"
FILES = {
    "db.json": "29c2640ee85e02cf5d9f3824a9d8f94788d26181e6db183e60dc13ed38e0a580",
    "tasks.json": "e603e45b650bafed5b3509d6da80dac0e5f03ca1908cb1e86b0edd4e64cab75c",
    "split_tasks.json": "4895e8eedcadecf84b2fc3b73a980447a8739b17d0bc50a33410a19475040cb3",
    "policy.md": "af10a9e616430e8bbd19b52e9e5096444dc76f9f73c7451aa163a831752923f8",
}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bad = []
    for name, want in FILES.items():
        path = DATA_DIR / name
        if not path.exists() or sha256(path) != want:
            with urllib.request.urlopen(f"{BASE}/{name}", timeout=120) as r:
                path.write_bytes(r.read())
        got = sha256(path)
        print(f"{name:18s} {got} {'ok' if got == want else 'MISMATCH'}")
        if got != want:
            bad.append(name)
    if bad:
        sys.exit(f"hash mismatch: {bad}; the pinned revision no longer serves the same data")


if __name__ == "__main__":
    main()
