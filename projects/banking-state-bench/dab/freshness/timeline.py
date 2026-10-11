"""The replay timeline (FRESHNESS.md §4): events, the clock, checkpoints and the oracle. No I/O.

Clock time is seconds since a pass starts. Each simulated day from 1 July is one block:
  [block start, + NIGHT_S)        night: nothing is written; the warehouse loads at NIGHT_S / 2
  [+ NIGHT_S, + BLOCK_S)          business hours 08:00-22:00, compressed into BUSINESS_S
A transaction dated 1 July or later is an event at a generated time of day (the data has dates
only): a minute drawn from a hash of its reference id, with one account's same-day transactions
kept in their ledger order. Clock time stops during a bank-wide question (driver.py), so every
time here is in clock seconds, never wall-clock seconds.
"""
import copy
import hashlib
from datetime import date, timedelta

START = date(2026, 7, 1)
DAYS = 6                              # 1-6 July; questions are asked on 1-5 July
NIGHT_S = 120
BUSINESS_S = 62 * 60
BLOCK_S = NIGHT_S + BUSINESS_S
OPEN_MIN, CLOSE_MIN = 8 * 60, 22 * 60  # business hours, minutes after midnight
MARGIN_S = 60                         # a question's accounts are quiet this long before it
CAP_S = 600                           # an episode's wall-clock cap
SLOTS_PER_DAY = 5
SLOT_SPACING_S = 750                  # 12.5 minutes between checkpoints
FIRST_SLOT_S = 60                     # the first checkpoint, after the day opens


def day_of(t):
    return min(int(t // BLOCK_S), DAYS - 1)


def day_date(d):
    return START + timedelta(days=d)


def clock_of(d, minute):
    """Clock time of minute-of-day `minute` (08:00-22:00) on day d."""
    return d * BLOCK_S + NIGHT_S + (minute - OPEN_MIN) / (CLOSE_MIN - OPEN_MIN) * BUSINESS_S


def sim_time(t):
    """The simulated time at clock t, as 'YYYY-MM-DD HH:MM'."""
    d = day_of(t)
    off = t - d * BLOCK_S
    if off < NIGHT_S:
        minute = 0
    else:
        minute = OPEN_MIN + (off - NIGHT_S) / BUSINESS_S * (CLOSE_MIN - OPEN_MIN)
    minute = int(min(minute, 24 * 60 - 1))
    return f"{day_date(d).isoformat()} {minute // 60:02d}:{minute % 60:02d}"


def load_time(t):
    """Clock time of the last nightly warehouse load at or before t."""
    return day_of(t) * BLOCK_S + NIGHT_S / 2


def checkpoints():
    """Clock times of the checkpoint slots, as (day, slot, t)."""
    return [(d, k, d * BLOCK_S + NIGHT_S + FIRST_SLOT_S + k * SLOT_SPACING_S)
            for d in range(DAYS - 1) for k in range(SLOTS_PER_DAY)]


def _minute(ref):
    h = int.from_bytes(hashlib.sha256(ref.encode()).digest()[:8], "big")
    return OPEN_MIN + h % (CLOSE_MIN - OPEN_MIN)


def events(customers):
    """Every transaction dated 1 July or later, as an event, in clock order.

    The upstream lists are newest first, and each transaction's balance_after follows from the one
    before it, so one account's same-day transactions keep that order: their hashed minutes are
    sorted and handed out oldest first."""
    out = []
    for cid, c in customers.items():
        for acc_id, a in c["accounts"].items():
            new = [t for t in reversed(a["transactions"]) if t["date"] >= START.isoformat()]
            by_day = {}
            for t in new:
                by_day.setdefault(t["date"], []).append(t)
            for day, ts in by_day.items():
                d = (date.fromisoformat(day) - START).days
                for t, m in zip(ts, sorted(_minute(t["reference_id"]) for t in ts)):
                    out.append({"t": clock_of(d, m), "ref": t["reference_id"], "account_id": acc_id,
                                "customer_id": cid, "minute": m, "txn": t})
    # clock order; ties keep one account's ledger order, as the sort is stable
    out.sort(key=lambda e: e["t"])
    return out


def state_at(customers, applied):
    """The upstream records with only the events in `applied` (a set of reference ids) posted.

    A transaction dated before 1 July is always posted. An account's balance is its newest posted
    transaction's balance_after, or, if none, its oldest transaction's balance before it."""
    out = copy.deepcopy(customers)
    first = START.isoformat()
    for c in out.values():
        for a in c["accounts"].values():
            full = a["transactions"]
            kept = [t for t in full if t["date"] < first or t["reference_id"] in applied]
            a["transactions"] = kept
            if kept:
                a["available_balance"] = kept[0]["balance_after"]
            else:
                oldest = full[-1]
                a["available_balance"] = round(oldest["balance_after"] - oldest["amount"], 2)
    return out


def applied_by(evts, t):
    """Reference ids of the events at or before clock t."""
    return {e["ref"] for e in evts if e["t"] <= t}


def touched(evts, accounts, t0, t1):
    """Events on any of `accounts` with t0 < time <= t1."""
    return [e for e in evts if e["account_id"] in accounts and t0 < e["t"] <= t1]
