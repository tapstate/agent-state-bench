"""Part B questions: "as of <checkpoint date>" questions over crmarenapro, with reference SQL.

Each question names its date, so its answer changes only through the data. The reference SQL
runs on the live source database `crm` right after a checkpoint's batch is applied; it cleans
identifiers and statuses itself (trim, leading '#'), independently of Tapstate.

`change` says which change type the question is sensitive to; controls read pre-cutoff records
only and must not change across checkpoints.
"""
TS = "trim({c})::timestamptz"
DAY = "substr(trim({c}), 1, 10)::date"
ID = "trim(both ' #' from {c})"

QUESTIONS = [
    {"id": 1, "change": "correction",
     "text": "As of {d}, how many support cases have a status other than Closed?",
     "sql": "select count(*) from support_case where trim(status) <> 'Closed'"},
    {"id": 2, "change": "correction",
     "text": "As of {d}, which user owns the most support cases with a status other than Closed? Give the user's full name; "
             "if several users tie, name all of them.",
     "sql": f"""with o as (select {ID.format(c='ownerid')} oid, count(*) n from support_case
                where trim(status) <> 'Closed' group by 1)
                select trim(u.firstname) || ' ' || trim(u.lastname) from o join crm_user u on {ID.format(c='u.id')} = o.oid
                where o.n = (select max(n) from o) order by 1"""},
    {"id": 3, "change": "correction",
     "text": "As of {d}, what is the average number of hours from creation to closure for the support cases "
             "closed on or after {d-365} and before {d}? Round to one decimal place.",
     "sql": f"""select round(avg(extract(epoch from {TS.format(c='closeddate')} - {TS.format(c='createddate')}) / 3600)::numeric, 1)
                from support_case where closeddate is not null and trim(closeddate) <> ''
                and {DAY.format(c='closeddate')} >= date '{{d}}' - 365 and {DAY.format(c='closeddate')} < date '{{d}}'"""},
    {"id": 4, "change": "correction",
     "text": "As of {d}, how many leads have the status Converted?",
     "sql": "select count(*) from lead where trim(status) = 'Converted'"},
    {"id": 5, "change": "correction",
     "text": "As of {d}, of the leads created on or after {d-365} and before {d}, what percentage have the status "
             "Converted? Round to one decimal place.",
     "sql": f"""select round(100.0 * sum(case when trim(status) = 'Converted' then 1 else 0 end) / count(*), 1)
                from lead where {DAY.format(c='createddate')} >= date '{{d}}' - 365 and {DAY.format(c='createddate')} < date '{{d}}'"""},
    {"id": 6, "change": "insert",
     "text": "As of {d}, what is the total amount of the opportunities whose stage is not Closed and whose close date "
             "is on or after {d} and before {d+90}? Round to two decimal places.",
     "sql": f"""select round(sum(amount)::numeric, 2) from opportunity where trim(stagename) <> 'Closed'
                and {DAY.format(c='closedate')} >= date '{{d}}' and {DAY.format(c='closedate')} < date '{{d}}' + 90"""},
    {"id": 7, "change": "insert",
     "text": "As of {d}, which account has the most opportunities created on or after {d-180} and before {d}? "
             "Give the account name; if several accounts tie, name all of them.",
     "sql": f"""with o as (select {ID.format(c='accountid')} aid, count(*) n from opportunity
                where {DAY.format(c='createddate')} >= date '{{d}}' - 180 and {DAY.format(c='createddate')} < date '{{d}}' group by 1)
                select trim(a.name) from o join account a on {ID.format(c='a.id')} = o.aid
                where o.n = (select max(n) from o) order by 1"""},
    {"id": 8, "change": "insert",
     "text": "As of {d}, how many quotes were created on or after {d-30} and before {d}?",
     "sql": f"select count(*) from quote where {DAY.format(c='createddate')} >= date '{{d}}' - 30 and {DAY.format(c='createddate')} < date '{{d}}'"},
    {"id": 9, "change": "backdated",
     "text": "As of {d}, how many orders have an effective date on or after {d-365} and before {d}?",
     "sql": f"select count(*) from sales_order where {DAY.format(c='effectivedate')} >= date '{{d}}' - 365 and {DAY.format(c='effectivedate')} < date '{{d}}'"},
    {"id": 10, "change": "backdated",
     "text": "As of {d}, what is the total quantity of items on the orders with an effective date on or after "
             "{d-180} and before {d}?",
     "sql": f"""select coalesce(sum(trim(i.quantity)::numeric), 0) from orderitem i join sales_order o on {ID.format(c='i.orderid')} = {ID.format(c='o.id')}
                where {DAY.format(c='o.effectivedate')} >= date '{{d}}' - 180 and {DAY.format(c='o.effectivedate')} < date '{{d}}'"""},
    {"id": 11, "change": "retraction",
     "text": "As of {d}, how many leads are there in total?",
     "sql": "select count(*) from lead"},
    {"id": 12, "change": "competing",
     "text": "As of {d}, how many support cases with a status other than Closed have priority High?",
     "sql": "select count(*) from support_case where trim(status) <> 'Closed' and trim(priority) = 'High'"},
    {"id": 13, "change": "correction",
     "text": "As of {d}, how many distinct accounts have at least one support case with a status other than Closed?",
     "sql": f"select count(distinct {ID.format(c='accountid')}) from support_case where trim(status) <> 'Closed'"},
    {"id": 14, "change": "control",
     "text": "How many support cases were created in 2021?",
     "sql": f"select count(*) from support_case where {DAY.format(c='createddate')} between date '2021-01-01' and date '2021-12-31'"},
    {"id": 15, "change": "control",
     "text": "Which account had the most orders with an effective date in 2022? Give the account name; if several "
             "accounts tie, name all of them.",
     "sql": f"""with o as (select {ID.format(c='accountid')} aid, count(*) n from sales_order
                where {DAY.format(c='effectivedate')} between date '2022-01-01' and date '2022-12-31' group by 1)
                select trim(a.name) from o join account a on {ID.format(c='a.id')} = o.aid
                where o.n = (select max(n) from o) order by 1"""},
]


def render(text, asof):
    """Fill {d}, {d-N} and {d+N} with ISO dates, so every range in a question is explicit."""
    import datetime as dt
    import re
    base = dt.date.fromisoformat(asof)
    return re.sub(r"\{d(?:([+-])(\d+))?\}",
                  lambda m: str(base + dt.timedelta(days=(int(m[2]) if m[1] == "+" else -int(m[2])) if m[1] else 0)),
                  text)


def ground_truth(cur, q, asof):
    """The rows the reference SQL returns at this checkpoint, as strings."""
    cur.execute(q["sql"].replace("{d}", asof))
    return [("" if r[0] is None else str(r[0])) for r in cur.fetchall()]


VALIDATE_PY = '''"""Generated for one Part B question at one checkpoint."""
import re

EXPECTED = {expected!r}
KIND = {kind!r}


def numbers(text):
    return [float(x.replace(",", "")) for x in re.findall(r"-?\\d[\\d,]*(?:\\.\\d+)?", text)]


def validate(llm_output: str):
    if KIND == "number":
        want = float(EXPECTED[0])
        tol = 0.051 if "." in EXPECTED[0] and len(EXPECTED[0].split(".")[1]) == 1 else (0.011 if "." in EXPECTED[0] else 0)
        got = numbers(llm_output)
        if any(abs(g - want) <= tol for g in got):
            return True, f"found {{EXPECTED[0]}}"
        return False, f"expected {{EXPECTED[0]}}, found {{got[:5]}}"
    missing = [e for e in EXPECTED if e.lower() not in llm_output.lower()]
    if missing:
        return False, f"missing {{missing}}"
    return True, "all expected names present"
'''


def write_query_dir(qdir, q, asof, truth):
    import json
    qdir.mkdir(parents=True, exist_ok=True)
    (qdir / "query.json").write_text(json.dumps(render(q["text"], asof)))
    (qdir / "ground_truth.csv").write_text("\n".join(truth) + "\n")
    kind = "number" if len(truth) == 1 and truth[0].replace(".", "", 1).replace("-", "", 1).isdigit() else "names"
    (qdir / "validate.py").write_text(VALIDATE_PY.format(expected=truth, kind=kind))
