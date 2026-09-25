import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for d in ("harness", "datasets", "eval"):
    sys.path.insert(0, str(ROOT / d))
