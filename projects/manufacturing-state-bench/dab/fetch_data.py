"""Download Microsoft's AdventureWorks OLTP install script (MIT) and unzip it into dab/data/raw/.

The archive is checked against a pinned SHA-256 before anything is extracted, so every build
starts from the same bytes. Files land in dab/data/ (not committed).

Usage: python fetch_data.py
"""
import hashlib
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = DATA / "raw"
URL = ("https://github.com/Microsoft/sql-server-samples/releases/download/adventureworks/"
       "AdventureWorks-oltp-install-script.zip")
SHA256 = "58962e94ea386ef7cd3d8a08211bfd42a79d9b81bdd68fd4b6b0051de6c5bd42"
ZIP = DATA / "AdventureWorks-oltp-install-script.zip"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    if not ZIP.exists() or sha256(ZIP) != SHA256:
        with urllib.request.urlopen(URL, timeout=300) as r:
            ZIP.write_bytes(r.read())
    got = sha256(ZIP)
    print(f"{ZIP.name} {got} {'ok' if got == SHA256 else 'MISMATCH'}")
    if got != SHA256:
        sys.exit("hash mismatch: the release no longer serves the pinned archive")
    RAW.mkdir(exist_ok=True)
    with zipfile.ZipFile(ZIP) as z:
        for m in z.infolist():
            name = Path(m.filename).name           # flat: the archive's files sit in one folder
            if m.is_dir() or not name:
                continue
            (RAW / name).write_bytes(z.read(m))
    print(f"unzipped {len(list(RAW.iterdir()))} files into {RAW}")


if __name__ == "__main__":
    main()
