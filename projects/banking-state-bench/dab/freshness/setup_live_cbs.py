"""Make bank_cbs the one live core banking database that both the agents and Tapstate read
(FRESHNESS.md §4). Idempotent.

- DATE columns become ISO text. On Tapstate 0.6.0 a DATE can't be read in a js step
  (tapstate/tapstate#681); the agents see the same text in every arm.
- Every table gets REPLICA IDENTITY FULL, so a delete's change event carries the whole row (the
  rewind deletes transactions, and Tapstate needs the row's account to remove it from its parent).

Usage: python setup_live_cbs.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fresh_common import pg  # noqa: E402

DATE_COLUMNS = (("accounts", "open_date"), ("transactions", "txn_date"), ("deposits", "start_date"),
                ("deposits", "maturity_date"))
TABLES = ("customers", "accounts", "account_holders", "transactions", "cheques", "deposits")


def main():
    with pg() as c, c.cursor() as cur:
        for t, col in DATE_COLUMNS:
            cur.execute("SELECT data_type FROM information_schema.columns WHERE table_schema = 'public' "
                        "AND table_name = %s AND column_name = %s", (t, col))
            if cur.fetchone()[0] == "date":
                cur.execute(f"ALTER TABLE {t} ALTER COLUMN {col} TYPE TEXT")
        for t in TABLES:
            cur.execute(f"ALTER TABLE {t} REPLICA IDENTITY FULL")
    print("bank_cbs: dates are ISO text, replica identity full")


if __name__ == "__main__":
    main()
