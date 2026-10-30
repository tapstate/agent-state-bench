"""Connections, names and paths for the freshness extension (FRESHNESS.md)."""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from common import MONGO_URI, PG  # noqa: E402

CBS_DB = "bank_cbs"                 # the live core banking system: agents and Tapstate read it
VIEW = ("views", "bank_customer")   # Tapstate's view; that database also holds other projects' views
LIVE_DB = "bank_live"               # U/C: a continuous mirror of the view, so the agent sees only it
WAREHOUSE_DB = "bank_warehouse"     # U/P: the view copied once a night
COLLECTION = "customer"
META = "_meta"                      # U/P only: when the warehouse was last loaded

# DAB dataset directories, one per arm (FRESHNESS.md §3)
DATASETS = {"D": "bank_fresh_direct", "UC": "bank_fresh_live", "UP": "bank_fresh_warehouse"}
ARMS = {arm: (ds, False) for arm, ds in DATASETS.items()}   # run_claude_arms' arm table: no hints

# question sets (FRESHNESS.md §14): "core" (questions 1-24) and "xs", cross-system only (25-48). The set
# is chosen with FRESH_SET; each has its own frozen question file and its own runs/ directory.
QSET = os.environ.get("FRESH_SET", "core")
assert QSET in ("core", "xs"), QSET
QUESTIONS = HERE / ("freshness_questions.json" if QSET == "core" else f"freshness_questions_{QSET}.json")
RUNS = HERE / "runs" if QSET == "core" else HERE / "runs" / QSET


def pg(db=CBS_DB):
    import psycopg2
    c = psycopg2.connect(dbname=db, **PG)
    c.autocommit = True
    return c


def mongo():
    from pymongo import MongoClient
    return MongoClient(MONGO_URI)
