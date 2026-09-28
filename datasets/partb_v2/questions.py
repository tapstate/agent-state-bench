"""Part B v2 questions: decisions an operational agent takes now, over a TPC-C database.

Each question is asked at a question time T. Its parameters (which customer, district or item) are
drawn from the state at T with a fixed seed, favoring entities the workload touched recently, so a
copy taken before T can disagree. Ground truth is reference SQL run against the source at T.

`velocity` tags how fast an answer changes under the TPC-C mix: fast (changes with most
transactions in its scope), slow (changes only with some transaction types), control (never changes
under the workload: items, customer identity).
"""
import random

# id, velocity, text template, parameter picker, reference SQL (psycopg2 %(name)s parameters)
QUESTIONS = [
    {"id": 1, "velocity": "fast",
     "text": "What is the current balance of customer {c_id} in district {d_id} of warehouse 1? "
             "Return the amount with two decimals.",
     "pick": "recent_customer",
     "sql": "select round(c_balance::numeric, 2) from customer where c_w_id = 1 and c_d_id = %(d_id)s "
            "and c_id = %(c_id)s"},
    {"id": 2, "velocity": "fast",
     "text": "Customer {c_id} in district {d_id} of warehouse 1 is asking about their most recent order. "
             "What is its order id, and has it been delivered yet (yes or no)?",
     "pick": "recent_customer",
     "sql": "select o_id, case when o_carrier_id is null then 'no' else 'yes' end from orders "
            "where o_w_id = 1 and o_d_id = %(d_id)s and o_c_id = %(c_id)s order by o_id desc limit 1"},
    {"id": 3, "velocity": "fast",
     "text": "How many orders in district {d_id} of warehouse 1 are waiting for delivery right now?",
     "pick": "district",
     "sql": "select count(*) from new_order where no_w_id = 1 and no_d_id = %(d_id)s"},
    {"id": 4, "velocity": "fast",
     "text": "Which undelivered order in district {d_id} of warehouse 1 has waited longest, i.e. is next "
             "in line for delivery? Return its order id.",
     "pick": "district",
     "sql": "select min(no_o_id) from new_order where no_w_id = 1 and no_d_id = %(d_id)s"},
    {"id": 5, "velocity": "fast",
     "text": "Among the items on the 20 most recent orders of district {d_id} in warehouse 1, how many "
             "distinct items have fewer than 15 units in stock in warehouse 1?",
     "pick": "district",
     "sql": "select count(distinct s_i_id) from order_line join stock on s_w_id = ol_w_id and s_i_id = ol_i_id "
            "where ol_w_id = 1 and ol_d_id = %(d_id)s and s_quantity < 15 and ol_o_id >= "
            "(select d_next_o_id - 20 from district where d_w_id = 1 and d_id = %(d_id)s) and ol_o_id < "
            "(select d_next_o_id from district where d_w_id = 1 and d_id = %(d_id)s)"},
    {"id": 6, "velocity": "fast",
     "text": "Can warehouse 1 ship {qty} units of item {i_id} from stock right now (yes or no)? "
             "Also return the units currently in stock.",
     "pick": "recent_item",
     "sql": "select case when s_quantity >= %(qty)s then 'yes' else 'no' end, s_quantity from stock "
            "where s_w_id = 1 and s_i_id = %(i_id)s"},
    {"id": 7, "velocity": "fast",
     "text": "How many orders has customer {c_id} in district {d_id} of warehouse 1 placed in total?",
     "pick": "recent_customer",
     "sql": "select count(*) from orders where o_w_id = 1 and o_d_id = %(d_id)s and o_c_id = %(c_id)s"},
    {"id": 8, "velocity": "slow",
     "text": "How many payments has customer {c_id} in district {d_id} of warehouse 1 made, and what is "
             "their year-to-date payment total? Return the count and the total with two decimals.",
     "pick": "recent_customer",
     "sql": "select c_payment_cnt, round(c_ytd_payment::numeric, 2) from customer where c_w_id = 1 and "
            "c_d_id = %(d_id)s and c_id = %(c_id)s"},
    {"id": 9, "velocity": "slow",
     "text": "Which 3 customers of district {d_id} in warehouse 1 owe the most right now, i.e. have the "
             "highest balance? Return their customer ids, highest first.",
     "pick": "district",
     "sql": "select c_id from customer where c_w_id = 1 and c_d_id = %(d_id)s "
            "order by c_balance desc, c_id limit 3"},
    {"id": 10, "velocity": "slow",
     "text": "What is the year-to-date payment total recorded for district {d_id} of warehouse 1? "
             "Return it with two decimals.",
     "pick": "district",
     "sql": "select round(d_ytd::numeric, 2) from district where d_w_id = 1 and d_id = %(d_id)s"},
    {"id": 11, "velocity": "slow",
     "text": "How many order lines of customer {c_id}'s orders in district {d_id} of warehouse 1 have "
             "not been delivered yet?",
     "pick": "recent_customer",
     "sql": "select count(*) from order_line join orders on o_w_id = ol_w_id and o_d_id = ol_d_id and "
            "o_id = ol_o_id where o_w_id = 1 and o_d_id = %(d_id)s and o_c_id = %(c_id)s and "
            "ol_delivery_d is null"},
    {"id": 12, "velocity": "slow",
     "text": "How many units of item {i_id} has warehouse 1 sold in total (year to date)?",
     "pick": "recent_item",
     "sql": "select s_ytd from stock where s_w_id = 1 and s_i_id = %(i_id)s"},
    {"id": 13, "velocity": "control",
     "text": "What is the list price of item {i_id}? Return it with two decimals.",
     "pick": "recent_item",
     "sql": "select round(i_price::numeric, 2) from item where i_id = %(i_id)s"},
    {"id": 14, "velocity": "control",
     "text": "What are the first and last name of customer {c_id} in district {d_id} of warehouse 1, and "
             "what is their credit status (GC or BC)?",
     "pick": "recent_customer",
     "sql": "select c_first, c_last, c_credit from customer where c_w_id = 1 and c_d_id = %(d_id)s and "
            "c_id = %(c_id)s"},
    {"id": 15, "velocity": "control",
     "text": "What is the name of item {i_id}?",
     "pick": "recent_item",
     "sql": "select i_name from item where i_id = %(i_id)s"},
]


def pick_params(cur, kind, rng):
    """Parameters for a question at the current state; prefer entities changed by recent transactions."""
    if kind == "district":
        return {"d_id": rng.randint(1, 10)}
    if kind == "recent_customer":
        cur.execute("select o_d_id, o_c_id from orders where o_w_id = 1 order by o_entry_d desc, o_id desc "
                    "limit 200")
        d_id, c_id = rng.choice(cur.fetchall())
        return {"d_id": d_id, "c_id": c_id}
    if kind == "recent_item":
        cur.execute("select ol_i_id, ol_quantity from order_line where ol_w_id = 1 "
                    "order by ol_o_id desc, ol_number limit 500")
        i_id, qty = rng.choice(cur.fetchall())
        return {"i_id": i_id, "qty": max(1, int(qty) * 3)}
    raise ValueError(kind)


def instantiate(cur, seed):
    """All questions with parameters and ground truth at the current source state."""
    rng = random.Random(seed)
    out = []
    for q in QUESTIONS:
        params = pick_params(cur, q["pick"], rng)
        cur.execute(q["sql"], params)
        truth = [list(r) for r in cur.fetchall()]
        out.append({**q, "params": params, "text": q["text"].format(**params), "truth": truth})
    return out


VALIDATE_PY = '''"""Generated for one Part B v2 question at one question time."""
import re

EXPECTED = {expected!r}   # every value of the reference result, flattened


def numbers(text):
    return [float(x.replace(",", "")) for x in re.findall(r"-?\\d[\\d,]*(?:\\.\\d+)?", text)]


def validate(llm_output: str):
    text = llm_output.lower()
    got = numbers(llm_output)
    for e in EXPECTED:
        s = str(e).strip()
        if s.lower() in ("yes", "no"):
            # "no" may be stated as "not" ("has not been delivered"); a clear answer carries only one side
            said_yes = bool(re.search(r"\\byes\\b", text))
            if s.lower() == "yes":
                ok = said_yes and not re.search(r"\\bno\\b", text)
            else:
                ok = not said_yes and bool(re.search(r"\\b(no|not)\\b", text))
            if not ok:
                return False, f"expected a clear {{s}}"
            continue
        try:
            want = float(s)
        except ValueError:
            if s.lower() not in text:
                return False, f"missing {{s}}"
            continue
        tol = 0.011 if "." in s else 0
        if not any(abs(g - want) <= tol for g in got):
            return False, f"missing {{s}}"
    return True, "all expected values present"
'''


def write_query_dir(qdir, q):
    """One DAB-style question directory: query.json, ground_truth.csv, validate.py."""
    import json
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "query.json").write_text(json.dumps(q["text"]))
    flat = [str(v) for row in q["truth"] for v in row]
    (qdir / "ground_truth.csv").write_text("\n".join(",".join(str(v) for v in row) for row in q["truth"]) + "\n")
    (qdir / "validate.py").write_text(VALIDATE_PY.format(expected=flat))
