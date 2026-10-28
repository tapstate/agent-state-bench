"""The freshness questions (FRESHNESS.md §5, §14): each fixed to a checkpoint, with its answer at the
checkpoint and the answer a warehouse loaded at the last midnight would give. Two sets of 24, chosen
with FRESH_SET: "core" (questions 1-24, mostly single-system) and "xs" (25-48, every question joins
core banking's balances with at least one other system).

Kinds:
  changed    about accounts with a transaction between the last load and the checkpoint (at least
             MARGIN_S before it); the current and warehouse answers differ
  control    about accounts with no transaction from the last load until the episode cap ends;
             the two answers are equal
  portfolio  across every account; the writer and clock stop at the checkpoint (driver.py)
For changed and control questions, the accounts involved have no transaction from MARGIN_S before
the checkpoint to CAP_S after it, so the answer can't change while an agent is answering.

Candidates are chosen deterministically (a hash of the slot and template) and no customer is used
twice. Writes the set's frozen record (fresh_common.QUESTIONS) with --write; otherwise checks that
the frozen record still matches.

Usage: [FRESH_SET=xs] python fresh_questions.py [--write]
"""
import hashlib
import json
import sys

from fresh_common import QSET, QUESTIONS  # (also puts dab/ on sys.path)
import timeline as tl
from common import load_customers

TOL = 1.0
SAVINGS_LIMITS = (100000, 75000, 250000)   # the first that separates the two answers is used

# per set: slot (day, k) -> kind, and the template order within a kind
PLANS = {
    "core": dict(
        first_id=1,
        control_slots=[(d, 0) for d in range(5)] + [(4, 4)],
        portfolio_slots=[(1, 4), (3, 4)],
        unused_slots=[(0, 1)],
        changed=["balance", "last_txn", "emi", "emi_premium", "cust_total", "cust_debits",
                 "balance", "last_txn", "emi", "emi_premium", "cust_total", "cust_debits",
                 "balance", "last_txn", "emi", "emi_premium"],
        control=["balance", "emi", "cust_total", "emi_premium", "balance", "cust_total"],
        portfolio=["savings_below", "hni_total"]),
    "xs": dict(
        first_id=25,
        control_slots=[(d, 0) for d in range(5)] + [(4, 4)],
        portfolio_slots=[(0, 4), (1, 4), (2, 4), (3, 4)],
        unused_slots=[(0, 1)],
        changed=["mandate_cover", "spend_power", "net_position", "emi_premium", "obligations",
                 "mandate_cover", "spend_power", "net_position", "emi_premium", "obligations",
                 "mandate_cover", "spend_power", "net_position", "obligations"],
        control=["spend_power", "net_position", "mandate_cover", "obligations", "emi_premium", "spend_power"],
        portfolio=["affluent_total", "home_loan_total", "mandate_accounts_total", "credit_card_total"]),
}
SYSTEMS = {"balance": ["core_banking"], "last_txn": ["core_banking"], "cust_total": ["core_banking"],
           "cust_debits": ["core_banking"], "emi": ["core_banking", "lending"],
           "emi_premium": ["core_banking", "lending", "insurance"],
           "savings_below": ["core_banking"], "hni_total": ["core_banking", "crm"],
           "mandate_cover": ["core_banking", "payments"], "spend_power": ["core_banking", "cards"],
           "net_position": ["core_banking", "lending"],
           "obligations": ["core_banking", "lending", "insurance", "payments"],
           "affluent_total": ["core_banking", "crm"], "home_loan_total": ["core_banking", "lending"],
           "mandate_accounts_total": ["core_banking", "payments"],
           "credit_card_total": ["core_banking", "cards"]}


def money(x):
    return ["money", round(x, 2), TOL]


def savings_account(c):
    return next(a for a in sorted(c["accounts"]) if c["accounts"][a]["account_type"] == "SAVINGS")


def active_mandates(c, account):
    return [m for m in c["mandates"] if m["account_id"] == account and m["status"] == "active"]


def active_credit_cards(c):
    return [k for k in c["cards"].values() if k["card_type"] == "CREDIT" and k["status"] == "active"]


def today_txns(a, day):
    return [t for t in a["transactions"] if t["date"] == day]


def template(name, cid, c_now, c_load, day):
    """(text, involved accounts, current spec, warehouse spec) for one customer, or None if the
    template does not apply to them."""
    def per(c):
        accs = c["accounts"]
        sav = savings_account(c)
        if name == "balance":
            return f"What is the available balance of account {sav} right now?", [sav], \
                money(accs[sav]["available_balance"])
        if name == "last_txn":
            txns = accs[sav]["transactions"]          # newest first
            if not txns or (len(txns) > 1 and txns[1]["date"] == txns[0]["date"]):
                return None   # none, or "most recent" is ambiguous: transactions carry a date only
            return f"What is the reference id of the most recent transaction on account {sav}?", [sav], \
                ["ids", [txns[0]["reference_id"]]]
        if name in ("emi", "emi_premium"):
            if not c["loans"] or (name == "emi_premium" and not c["insurance"]):
                return None
            due = sum(l["emi"] for l in c["loans"].values())
            what = "one EMI on each of their loans"
            if name == "emi_premium":
                due += sum(p["premium"] for p in c["insurance"].values())
                what = "one EMI on each of their loans and one premium on each of their insurance policies"
            return (f"If customer {cid} paid {what} from savings account {sav}, what would that account's "
                    f"available balance be afterwards?"), [sav], money(accs[sav]["available_balance"] - due)
        if name == "cust_total":
            if len(accs) < 2:
                return None
            return f"What is the total available balance across all of customer {cid}'s accounts right now?", \
                sorted(accs), money(sum(a["available_balance"] for a in accs.values()))
        if name == "cust_debits":
            if len(accs) < 2:
                return None
            total = -sum(t["amount"] for a in accs.values() for t in today_txns(a, day) if t["direction"] == "debit")
            return f"What is the total amount debited today across all of customer {cid}'s accounts?", \
                sorted(accs), money(total)
        total = sum(a["available_balance"] for a in accs.values())
        if name == "mandate_cover":
            ms = active_mandates(c, sav)
            if not ms:
                return None
            return (f"If each active payment mandate on customer {cid}'s savings account {sav} debited once, "
                    f"what would that account's available balance be afterwards?"), [sav], \
                money(accs[sav]["available_balance"] - sum(m["amount"] for m in ms))
        if name == "spend_power":
            cards = active_credit_cards(c)
            if not cards:
                return None
            return (f"What is customer {cid}'s total spending power right now: the available balance across all "
                    f"of their accounts plus the available limit on their active credit cards?"), sorted(accs), \
                money(total + sum(k["available_limit"] for k in cards))
        if name == "net_position":
            if not c["loans"]:
                return None
            # by magnitude: answer_check reads unsigned numbers, and agents state a shortfall as
            # "-₹x", "a deficit of ₹x" or "₹x short" alike
            return (f"What is customer {cid}'s net position right now: the total available balance across all of "
                    f"their accounts minus the total outstanding on their loans?"), sorted(accs), \
                money(abs(total - sum(l["outstanding"] for l in c["loans"].values())))
        if name == "obligations":
            ms = active_mandates(c, sav)
            if not c["loans"] or not c["insurance"] or not ms:
                return None
            due = (sum(l["emi"] for l in c["loans"].values()) + sum(p["premium"] for p in c["insurance"].values())
                   + sum(m["amount"] for m in ms))
            return (f"If customer {cid} paid one EMI on each of their loans and one premium on each of their "
                    f"insurance policies from savings account {sav}, and each active payment mandate on that "
                    f"account also debited once, what would its available balance be afterwards?"), [sav], \
                money(accs[sav]["available_balance"] - due)
        raise ValueError(name)
    now, load = per(c_now[cid]), per(c_load[cid])
    if now is None or load is None:
        return None
    return now[0], now[1], now[2], load[2]


def differs(a, b):
    if a[0] in ("count", "ids"):
        return a[1] != b[1]
    return abs(a[1] - b[1]) > 2 * TOL


def nonzero(spec):
    return spec[0] == "ids" or spec[1] != 0


def savings_below(cs, limit):
    return sum(a["account_type"] == "SAVINGS" and a["available_balance"] < limit
               for c in cs.values() for a in c["accounts"].values())


def total_where(cs, keep):
    """Total available balance across the accounts that keep(customer, account_id) selects."""
    return sum(a["available_balance"] for c in cs.values() for aid, a in c["accounts"].items() if keep(c, aid))


PORTFOLIO = {   # name -> (question, which accounts count)
    "hni_total": ("What is the total available balance right now across all accounts of customers in the HNI "
                  "segment?", lambda c, a: c["profile"]["segment"] == "HNI"),
    "affluent_total": ("What is the total available balance right now across all accounts of customers in the "
                       "AFFLUENT segment?", lambda c, a: c["profile"]["segment"] == "AFFLUENT"),
    "home_loan_total": ("What is the total available balance right now across all accounts of customers who have "
                        "a home loan?", lambda c, a: any(l["loan_type"] == "home_loan" for l in c["loans"].values())),
    "mandate_accounts_total": ("What is the total available balance right now across all accounts that have at "
                               "least one active payment mandate?", lambda c, a: bool(active_mandates(c, a))),
    "credit_card_total": ("What is the total available balance right now across all accounts of customers who hold "
                          "an active credit card?", lambda c, a: bool(active_credit_cards(c))),
}


def portfolio_spec(name, cs, limit=None):
    """A bank-wide question's answer in state cs."""
    if name == "savings_below":
        return ["count", savings_below(cs, limit)]
    return money(total_where(cs, PORTFOLIO[name][1]))


def portfolio(name, c_now, c_load):
    """(text, current spec, warehouse spec, limit); savings_below uses the first limit that separates them."""
    if name == "savings_below":
        for limit in SAVINGS_LIMITS:
            now, load = portfolio_spec(name, c_now, limit), portfolio_spec(name, c_load, limit)
            if now != load:
                return (f"How many savings accounts have an available balance below ₹{limit:,} right now?",
                        now, load, limit)
        raise SystemExit("no savings threshold separates the current and warehouse answers")
    return PORTFOLIO[name][0], portfolio_spec(name, c_now), portfolio_spec(name, c_load), None


def pick(cands, seed):
    h = int.from_bytes(hashlib.sha256(seed.encode()).digest()[:8], "big")
    return cands[h % len(cands)]


def build(qset=QSET):
    P = PLANS[qset]
    cs = load_customers()
    ev = tl.events(cs)
    slots = {(d, k): t for d, k, t in tl.checkpoints()}
    plan = ([(s, "control", n) for s, n in zip(P["control_slots"], P["control"])]
            + [(s, "portfolio", n) for s, n in zip(P["portfolio_slots"], P["portfolio"])])
    taken = set(P["control_slots"]) | set(P["portfolio_slots"]) | set(P["unused_slots"])
    plan += [(s, "changed", n) for s, n in zip(sorted(set(slots) - taken), P["changed"])]
    assert len(plan) == 24 and len({p[0] for p in plan}) == 24
    plan.sort(key=lambda p: slots[p[0]])

    used, out = set(), []
    for i, ((d, k), kind, name) in enumerate(plan, P["first_id"]):
        t = slots[(d, k)]
        day = tl.day_date(d).isoformat()
        c_now = tl.state_at(cs, tl.applied_by(ev, t))
        c_load = tl.state_at(cs, tl.applied_by(ev, d * tl.BLOCK_S))
        q = {"id": i, "day": d, "slot": k, "t": t, "sim_time": tl.sim_time(t), "kind": kind, "template": name,
             "systems": SYSTEMS[name]}
        if kind == "portfolio":
            text, spec, stale, limit = portfolio(name, c_now, c_load)
            q.update(customer_id=None, accounts=None, limit=limit)
        else:
            cands = []
            for cid in sorted(cs):
                if cid in used:
                    continue
                r = template(name, cid, c_now, c_load, day)
                if r is None:
                    continue
                text, accs, spec, stale = r
                if tl.touched(ev, set(accs), t - tl.MARGIN_S - 1, t + tl.CAP_S):
                    continue      # not quiet around the checkpoint
                since_load = tl.touched(ev, set(accs), d * tl.BLOCK_S, t + tl.CAP_S)
                if kind == "changed" and (not since_load or not differs(spec, stale) or not nonzero(spec)):
                    continue
                if kind == "control" and (since_load or not nonzero(spec)):
                    continue
                cands.append((cid, text, accs, spec, stale))
            if not cands:
                raise SystemExit(f"no customer fits question {i} ({kind} {name} at {q['sim_time']})")
            cid, text, accs, spec, stale = pick(cands, f"{d}-{k}-{name}")
            used.add(cid)
            q.update(customer_id=cid, accounts=accs, candidates=len(cands))
        if kind == "control":
            assert spec == stale
        else:
            assert differs(spec, stale), q
        q.update(text=f"Current time: {q['sim_time']} IST. {text}", answer=spec, warehouse_answer=stale)
        out.append(q)
    return out


def main():
    qs = json.loads(json.dumps(build(), ensure_ascii=False))
    if "--write" in sys.argv:
        QUESTIONS.write_text(json.dumps(qs, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {len(qs)} questions to {QUESTIONS.name}")
    else:
        frozen = json.loads(QUESTIONS.read_text())
        sys.exit(0 if frozen == qs else f"{QUESTIONS.name} differs from what fresh_questions.py builds")
    for q in qs:
        print(f"{q['id']:>2} {q['sim_time']} {q['kind']:<9} {q['template']:<13} "
              f"cands={q.get('candidates', '-'):<4} {q['answer']} (warehouse {q['warehouse_answer']})")


if __name__ == "__main__":
    main()
