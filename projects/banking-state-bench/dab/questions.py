"""The 24 pilot questions and their reference answers, computed from upstream db.json only.

Answers never read the split sources or setup C, so a split or consolidation error cannot leak
into the ground truth. Customer-scoped questions pick their customer by a fixed, stated rule.

Each question: id, category, the systems it needs (for the report), text, and an answer spec the
validator generator (make_queries.py) turns into validate.py:
  ("count", n)                   an integer that must appear as a standalone number
  ("money", x, tol)              a number within tol of x
  ("text", s)                    a string that must appear (case-insensitive)
  ("ids", [must])                every id in must appears
  ("labeled", [(label, spec)])   each spec is found within a window after its label
  ("all", [spec, ...])           every spec holds
"""
from collections import Counter, defaultdict

from common import load_customers

CONTROL, TWO, MULTI, PORTFOLIO = "single-source control", "two sources", "three or more sources", "portfolio"


def money(x):
    return round(x + 0.0, 2)


def accounts(c):
    return list(c["accounts"].values())


def cards(c):
    return list(c["cards"].values())


def loans(c):
    return list(c["loans"].values())


def state(c):
    return c["profile"]["communication_address"]["state"]


def colliding_ids(cs):
    """Card, deposit and loan ids that more than one customer uses."""
    out = set()
    for k in ("cards", "deposits", "loans"):
        n = Counter(i for c in cs.values() for i in c[k])
        out |= {i for i, v in n.items() if v > 1}
    return out


def unique_max(counter):
    (top, n), *rest = counter.most_common()
    assert not rest or rest[0][1] < n, f"tie for first place: {counter.most_common(3)}"
    return top, n


# --- customer-scoped picks -------------------------------------------------------------------
def pick(cs, ok):
    """The first customer, by customer_id, that meets the rule and owns no colliding id."""
    bad = colliding_ids(cs)
    for cid in sorted(cs):
        c = cs[cid]
        if ok(c) and not (set(c["cards"]) | set(c["deposits"]) | set(c["loans"])) & bad:
            return cid
    raise AssertionError("no customer meets the rule")


def rule_q5(c):   # two frozen cards, and more than one account they could be linked to
    return sum(1 for k in cards(c) if k["status"] == "frozen") == 2 and len(c["accounts"]) >= 2


def rule_q8(c):   # >= 2 active mandates on a savings account, and >= 1 that does not qualify
    sav = {a["account_id"] for a in accounts(c) if a["account_type"] == "SAVINGS"}
    good = [m for m in c["mandates"] if m["status"] == "active" and m["account_id"] in sav]
    return len(good) >= 2 and len(good) < len(c["mandates"])


def rule_q14(c):  # several accounts, a frozen and an unfrozen card, a loan, a service request
    st = Counter(k["status"] for k in cards(c))
    return len(c["accounts"]) >= 2 and st["frozen"] >= 1 and st["active"] >= 1 and c["loans"] and c["requests"]


def rule_q20(c):  # holds every product kind, with at least three different counts among them
    n = [len(c[k]) for k in ("accounts", "cards", "deposits", "loans", "insurance", "mandates")]
    return min(n) >= 1 and len(set(n)) >= 3


# --- questions -------------------------------------------------------------------------------
def build(cs=None):
    cs = cs or load_customers()
    Q = []

    def q(cat, systems, text, spec):
        Q.append({"id": len(Q) + 1, "category": cat, "systems": systems, "text": text, "answer": spec})

    allc = list(cs.values())

    # 1-4: single-source controls
    n = sum(1 for c in allc for d in c["deposits"].values()
            if d["kind"] == "RD" and "2026-10-01" <= d["maturity_date"] <= "2026-12-31")
    q(CONTROL, ["core_banking"],
      "How many recurring deposits (RD) have a maturity date between 2026-10-01 and 2026-12-31, inclusive?",
      ("count", n))
    n = sum(1 for c in allc for k in cards(c) if k["card_type"] == "CREDIT" and k["status"] == "frozen")
    q(CONTROL, ["cards"], "How many credit cards are currently frozen?", ("count", n))
    x = money(sum(l["outstanding"] for c in allc for l in loans(c) if l["loan_type"] == "home_loan"))
    q(CONTROL, ["lending"],
      "What is the total outstanding amount across all home loans? Give the amount in rupees to two decimals.",
      ("money", x, 1.0))
    top, n = unique_max(Counter(m["payee"] for c in allc for m in c["mandates"] if m["status"] == "paused"))
    q(CONTROL, ["payments"],
      "Which payee has the most paused mandates, and how many paused mandates does it have?",
      ("all", [("text", top), ("count", n)]))

    # 5-12: two sources
    cid = pick(cs, rule_q5)
    c = cs[cid]
    frozen = [k for k in cards(c) if k["status"] == "frozen"]
    q(TWO, ["core_banking", "cards"],
      f"Customer {cid} has frozen cards. List each frozen card's id and the id of the bank account it is "
      "linked to (an account id such as SB1785318658).",
      ("all", [("ids", [k["card_id"] for k in frozen])]
       + [("labeled", [(k["card_id"], ("text", k["linked_account"]))]) for k in frozen]))
    n = sum(1 for c in allc if c["profile"]["senior_citizen"] and any(l["loan_type"] == "gold_loan" for l in loans(c)))
    q(TWO, ["core_banking", "lending"], "How many senior-citizen customers hold at least one gold loan?",
      ("count", n))
    x = money(sum(a["available_balance"] for c in allc if c["profile"]["segment"] == "HNI"
                  for a in accounts(c) if a["account_type"] == "SAVINGS"))
    q(TWO, ["crm", "core_banking"],
      "What is the total available balance across all savings accounts held by customers in the HNI segment? "
      "Give the amount in rupees to two decimals.", ("money", x, 1.0))
    cid = pick(cs, rule_q8)
    c = cs[cid]
    sav = {a["account_id"] for a in accounts(c) if a["account_type"] == "SAVINGS"}
    good = [m for m in c["mandates"] if m["status"] == "active" and m["account_id"] in sav]
    q(TWO, ["core_banking", "payments"],
      f"For customer {cid}, list the ids of the active mandates that debit one of the customer's savings "
      "accounts, and give the total monthly amount of those mandates.",
      ("all", [("ids", [m["mandate_id"] for m in good]), ("money", sum(m["amount"] for m in good), 0.5)]))
    n = sum(1 for c in allc for r in c["requests"].values() if r["status"] == "OPEN" and c["profile"]["age"] >= 60)
    q(TWO, ["crm", "core_banking"], "How many open service requests belong to customers aged 60 or over?",
      ("count", n))
    n = sum(1 for c in allc if c["profile"]["senior_citizen"] and c["insurance"])
    q(TWO, ["core_banking", "insurance"], "How many senior-citizen customers hold at least one insurance policy?",
      ("count", n))
    top, n = unique_max(Counter(c["accounts"][k["linked_account"]]["home_branch"]
                                for c in allc for k in cards(c) if k["status"] == "frozen"))
    q(TWO, ["core_banking", "cards"],
      "Which home branch has the most frozen cards linked to accounts held at that branch, and how many frozen "
      "cards is that?", ("all", [("text", top), ("count", n)]))
    emis = [l["emi"] for c in allc if c["profile"]["segment"] == "MASS" for l in loans(c)
            if l["loan_type"] == "personal_loan"]
    q(TWO, ["crm", "lending"],
      "What is the average EMI of the personal loans held by customers in the MASS segment? Give the amount in "
      "rupees to two decimals.", ("money", money(sum(emis) / len(emis)), 0.01))

    # 13-20: three or more sources
    n = sum(1 for c in allc if c["profile"]["segment"] == "HNI"
            and any(l["loan_type"] == "home_loan" for l in loans(c))
            and any(m["status"] == "paused" for m in c["mandates"]))
    q(MULTI, ["crm", "lending", "payments", "core_banking"],
      "How many customers in the HNI segment hold a home loan and have at least one paused mandate?", ("count", n))
    cid = pick(cs, rule_q14)
    c = cs[cid]
    q(MULTI, ["core_banking", "cards", "lending", "crm"],
      f"For customer {cid}, report: (a) the total available balance across all of their accounts, (b) the "
      "number of their cards that are not frozen, (c) the total outstanding amount across their loans, and "
      "(d) the number of their open service requests. Give amounts in rupees to two decimals.",
      ("all", [("money", money(sum(a["available_balance"] for a in accounts(c))), 1.0),
               ("money", money(sum(l["outstanding"] for l in loans(c))), 1.0),
               ("labeled", [(["(b)", "not frozen", "unfrozen", "active card"],
                             ("count", sum(1 for k in cards(c) if k["status"] != "frozen"))),
                            (["(d)", "service request"],
                             ("count", sum(1 for r in c["requests"].values() if r["status"] == "OPEN")))])]))
    n = sum(1 for c in allc if state(c) == "Maharashtra" and c["insurance"]
            and any(l["loan_type"] == "home_loan" for l in loans(c)))
    q(MULTI, ["core_banking", "lending", "insurance"],
      "How many customers whose communication address is in Maharashtra hold both a home loan and at least one "
      "insurance policy?", ("count", n))
    n = sum(1 for c in allc for a in accounts(c) if a["account_type"] == "SAVINGS"
            and any(k["status"] == "frozen" and k["linked_account"] == a["account_id"] for k in cards(c))
            and any(m["status"] == "active" and m["type"] == "UPI_AUTOPAY" and m["account_id"] == a["account_id"]
                    for m in c["mandates"]))
    q(MULTI, ["core_banking", "cards", "payments"],
      "How many savings accounts have both a frozen card linked to them and an active UPI_AUTOPAY mandate "
      "debiting them?", ("count", n))
    n = sum(1 for c in allc if c["profile"]["sector"] == "Information Technology"
            and any(t["channel"] == "atm" and t["direction"] == "debit" and "2026-06-15" <= t["date"] <= "2026-06-30"
                    for a in accounts(c) for t in a["transactions"])
            and any(k["card_type"] == "DEBIT" and k["status"] == "frozen" for k in cards(c)))
    q(MULTI, ["crm", "core_banking", "cards"],
      "How many customers in the Information Technology sector made at least one ATM debit transaction between "
      "2026-06-15 and 2026-06-30 (inclusive) and also hold a frozen debit card?", ("count", n))
    by_state = defaultdict(float)
    for c in allc:
        for l in loans(c):
            if l["loan_type"] == "gold_loan":
                by_state[state(c)] += l["outstanding"]
    top = max(by_state, key=by_state.get)
    assert sorted(by_state.values())[-2] < by_state[top]
    q(MULTI, ["core_banking", "lending"],
      "Which state, by customers' communication address, has the highest total outstanding gold-loan amount, "
      "and what is that total? Give the amount in rupees to two decimals.",
      ("all", [("text", top), ("money", money(by_state[top]), 1.0)]))
    x = money(sum(p["sum_assured"] for c in allc if c["profile"]["segment"] == "AFFLUENT"
                  and c["profile"]["senior_citizen"] for p in c["insurance"].values()))
    q(MULTI, ["crm", "core_banking", "insurance"],
      "What is the total sum assured of the insurance policies held by senior-citizen customers in the AFFLUENT "
      "segment? Give the amount in rupees to two decimals.", ("money", x, 1.0))
    cid = pick(cs, rule_q20)
    c = cs[cid]
    q(MULTI, ["core_banking", "cards", "lending", "payments", "insurance"],
      f"For customer {cid}, how many of each of the following do they hold: accounts, cards, deposits, loans, "
      "insurance policies, and mandates? Answer in the form 'accounts: N, cards: N, deposits: N, loans: N, "
      "insurance policies: N, mandates: N'.",
      ("labeled", [(lbl, ("count", len(c[k]))) for lbl, k in
                   (("accounts", "accounts"), ("cards", "cards"), ("deposits", "deposits"), ("loans", "loans"),
                    ("insurance", "insurance"), ("mandates", "mandates"))]))

    # 21-24: portfolio aggregates
    seg = defaultdict(float)
    for c in allc:
        seg[c["profile"]["segment"]] += sum(l["outstanding"] for l in loans(c))
    q(PORTFOLIO, ["crm", "lending"],
      "For each customer segment (HNI, AFFLUENT, MASS), what is the total outstanding amount across all loans "
      "held by customers in that segment? Give amounts in rupees to two decimals.",
      ("labeled", [(s, ("money", money(seg[s]), 1.0)) for s in ("HNI", "AFFLUENT", "MASS")]))
    n = sum(1 for c in allc if c["deposits"] and c["loans"] and c["insurance"]
            and any(k["card_type"] == "CREDIT" for k in cards(c)))
    q(PORTFOLIO, ["core_banking", "lending", "insurance", "cards"],
      "How many customers hold all four of: a fixed or recurring deposit, a loan, an insurance policy, and a "
      "credit card?", ("count", n))
    x = money(sum(l["emi"] for c in allc if state(c) == "Kerala" for l in loans(c))
              + sum(m["amount"] for c in allc if state(c) == "Kerala" for m in c["mandates"]
                    if m["status"] == "active" and m["frequency"] == "monthly"))
    q(PORTFOLIO, ["core_banking", "lending", "payments"],
      "For customers whose communication address is in Kerala, what is the total of the monthly EMIs on their "
      "loans plus the amounts of their active monthly mandates? Give the amount in rupees to two decimals.",
      ("money", x, 1.0))
    n = sum(1 for c in allc
            if any(k["card_type"] == "CREDIT" and k["available_limit"] < 0.5 * k["credit_limit"] for k in cards(c))
            and any(a["account_type"] == "SAVINGS" and a["available_balance"] > 500000 for a in accounts(c)))
    q(PORTFOLIO, ["cards", "core_banking"],
      "How many customers have a credit card whose available limit is below half of its credit limit, and also a "
      "savings account with an available balance above 500000 rupees?", ("count", n))
    return Q


if __name__ == "__main__":
    import json
    for x in build():
        print(json.dumps(x, ensure_ascii=False))
