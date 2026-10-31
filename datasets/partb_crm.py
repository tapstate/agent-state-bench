"""Part B, freshness: replay crmarenapro's own timeline into its sources, checkpoint by checkpoint.

The source of truth for every checkpoint is the pristine copy `crm_orig` (the Postgres copy of
DAB's six crmarenapro databases, as loaded by load_crm_to_postgres.py). `state_at(k)` derives the
full content of every table at checkpoint k from it; `apply(k)` moves the live database `crm`
(which Tapstate reads through CDC) to that state with real INSERT / UPDATE / DELETE statements.

Change types, each present in every batch after checkpoint 0:
- in-place correction: case owner reassignments and closures, from casehistory__c; lead
  conversions, from converteddate
- late, backdated arrival: every fourth order after the cutoff is inserted one checkpoint after
  its effective date
- retraction: at checkpoints 2 and 4, some leads that arrived in the batch before are deleted as
  duplicates (synthetic)
- competing writes: per batch, some open cases get two priority writes in the same batch; only
  the last one may count (synthetic)
- no change: control questions read pre-cutoff records only

Reconstruction rules where the data holds final values only: a case open at the checkpoint whose
final status is Closed is 'Working'; a lead converted after the checkpoint is 'Working' with its
conversion fields empty. Records carrying no date (accounts, contacts, users, products, ...) are
present from the start.

Usage:
  python partb_crm.py apply <k>            move `crm` to checkpoint k (0 resets to the cutoff)
  python partb_crm.py export <k> <dab>     write query_crmlive_cp<k>: DAB's original layout, state k
  python partb_crm.py truth <k> <dab>      write each question's ground truth for checkpoint k
"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import duckdb
import psycopg2
from psycopg2.extras import execute_values

DSN = "host=127.0.0.1 port=55432 user=postgres password=secret"
CHECKPOINTS = ["2023-01-01", "2023-04-01", "2023-07-01", "2023-10-01", "2024-01-01", "2024-06-01"]
CUTOFF = CHECKPOINTS[0]
RETRACT_AT = {2: 0, 4: 1}          # checkpoint -> which half of the retraction set goes then

DATED = {  # table -> column whose date says when the record comes into existence
    "lead": "createddate", "opportunity": "createddate", "quote": "createddate",
    "sales_order": "effectivedate", "support_case": "createddate", "casehistory__c": "createddate",
    "emailmessage": "messagedate", "livechattranscript": "endtime", "voicecalltranscript__c": "createddate",
    "task": "activitydate", "event": "startdatetime", "contract": "startdate",
}
CHILDREN = {"opportunitylineitem": ("opportunityid", "opportunity"), "quotelineitem": ("quoteid", "quote"),
            "orderitem": ("orderid", "sales_order")}


def key(v):
    """Identifier as the sources mean it: dirt (a leading '#', surrounding spaces) removed."""
    return None if v is None else str(v).strip().lstrip("#").strip()


def day(v):
    return None if v in (None, "") else str(v).strip()[:10]


def pg(db):
    c = psycopg2.connect(f"{DSN} dbname={db}")
    c.autocommit = True
    return c


def load_orig():
    cur = pg("crm_orig").cursor()
    cur.execute("select table_name from information_schema.tables where table_schema='public'")
    tables = {}
    for (t,) in cur.fetchall():
        cur.execute(f'select * from "{t}"')
        cols = [d[0] for d in cur.description]
        tables[t] = (cols, [dict(zip(cols, r)) for r in cur.fetchall()])
    return tables


def retraction_set(orig):
    """Leads removed as duplicates: at checkpoint cp, every tenth lead that arrived in the batch
    before it (up to 8), deleted in batch cp."""
    out = []
    for cp in sorted(RETRACT_AT):
        lo, hi = CHECKPOINTS[cp - 2], CHECKPOINTS[cp - 1]
        arrived = sorted((r for r in orig["lead"][1] if lo <= day(r["createddate"]) < hi), key=lambda r: key(r["id"]))
        out.append(arrived[::10][:8])
    return out


def delayed_orders(orig):
    late = sorted((r for r in orig["sales_order"][1] if day(r["effectivedate"]) >= CUTOFF), key=lambda r: key(r["id"]))
    return {key(r["id"]) for r in late[::4]}


def checkpoint_index(d):
    """First checkpoint at or after date d."""
    for i, c in enumerate(CHECKPOINTS):
        if d <= c:
            return i
    return len(CHECKPOINTS)


def competing(orig, k):
    """Synthetic competing writes of batch k: (case id, intermediate priority, final priority)."""
    if k == 0:
        return []
    cases = sorted(orig["support_case"][1], key=lambda r: key(r["id"]))
    lo, hi = CHECKPOINTS[k - 1], CHECKPOINTS[k]
    open_then = [r for r in cases if day(r["createddate"]) < lo and not (r["closeddate"] and day(r["closeddate"]) <= hi)]
    out = []
    for i, r in enumerate(open_then[k::7][:6]):
        orig_p = r["priority"]
        # half flip and flip back (final unchanged), half escalate with a stray intermediate value
        out.append((key(r["id"]), "Low", orig_p) if i % 2 else (key(r["id"]), "Low", "High"))
    return out


def state_at(orig, k):
    t_end = CHECKPOINTS[k]
    retracted = set()
    for cp, half in RETRACT_AT.items():
        if k >= cp:
            retracted |= {key(r["id"]) for r in retraction_set(orig)[half]}
    late = delayed_orders(orig)
    escalated = {}
    for j in range(1, k + 1):
        for cid, _, final in competing(orig, j):
            escalated[cid] = final
    owners = {}
    for h in sorted(orig["casehistory__c"][1], key=lambda r: r["createddate"] or ""):
        if (h["field__c"] or "").strip() == "Owner Assignment" and day(h["createddate"]) < t_end and h["newvalue__c"]:
            owners[key(h["caseid__c"])] = h["newvalue__c"]
    out = {}
    for t, (cols, rows) in orig.items():
        keep = []
        for r in rows:
            r = dict(r)
            col = DATED.get(t)
            if col and r.get(col) and day(r[col]) >= t_end:
                continue
            if t == "sales_order" and key(r["id"]) in late and checkpoint_index(day(r["effectivedate"])) + 1 > k \
                    and day(r["effectivedate"]) >= CUTOFF:
                continue
            if t == "lead":
                if key(r["id"]) in retracted:
                    continue
                if r["converteddate"] and day(r["converteddate"]) >= t_end:
                    r.update(status="Working", converteddate=None, convertedcontactid=None,
                             convertedaccountid=None, isconverted=0)
            if t == "support_case":
                if r["closeddate"] and day(r["closeddate"]) >= t_end:
                    r["closeddate"] = None
                if not r["closeddate"] and (r["status"] or "").strip() == "Closed":
                    r["status"] = "Working"
                if key(r["id"]) in owners:
                    r["ownerid"] = owners[key(r["id"])]
                if key(r["id"]) in escalated:
                    r["priority"] = escalated[key(r["id"])]
            keep.append(r)
        out[t] = (cols, keep)
    for child, (fk, parent) in CHILDREN.items():
        alive = {key(r["id"]) for r in out[parent][1]}
        cols, rows = out[child]
        out[child] = (cols, [r for r in rows if key(r[fk]) in alive])
    return out


def apply(k, db="crm"):
    """Move the live database `crm` to checkpoint k with row-level writes, so CDC carries each
    change. Competing writes of batch k are issued first with their intermediate value."""
    orig = load_orig()
    want = state_at(orig, k)
    con = pg(db)
    cur = con.cursor()
    counts = {"insert": 0, "update": 0, "delete": 0, "competing": 0}
    for cid, mid, _ in competing(orig, k):
        cur.execute("update support_case set priority=%s where trim(both ' #' from id)=%s", (mid, cid))
        counts["competing"] += cur.rowcount
    for t, (cols, rows) in want.items():
        cur.execute(f'select * from "{t}"')
        names = [d[0] for d in cur.description]
        have = {row["id"]: row for row in (dict(zip(names, r)) for r in cur.fetchall())}
        target = {r["id"]: r for r in rows}
        for rid in set(have) - set(target):
            cur.execute(f'delete from "{t}" where id=%s', (rid,))
            counts["delete"] += 1
        ins = [tuple(target[rid][c] for c in cols) for rid in set(target) - set(have)]
        if ins:
            execute_values(cur, f'insert into "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) values %s', ins)
            counts["insert"] += len(ins)
        for rid in set(target) & set(have):
            diff = {c: target[rid][c] for c in cols if target[rid][c] != have[rid][c]}
            if diff:
                sets = ", ".join(f'"{c}"=%s' for c in diff)
                cur.execute(f'update "{t}" set {sets} where id=%s', (*diff.values(), rid))
                counts["update"] += 1
    # the marker written last: when it is visible downstream, every change before it is too
    cur.execute("create table if not exists partb_marker (id text primary key, checkpoint int, at timestamptz)")
    cur.execute("insert into partb_marker values ('m', %s, now()) on conflict (id) do update "
                "set checkpoint=excluded.checkpoint, at=excluded.at", (k,))
    return counts


# --- export to DAB's original six-database layout --------------------------------------------------

LAYOUT = {  # file / database -> tables, with their original names
    "core_crm.db": ["Account", "Contact", "User"],
    "products_orders.db": ["Order", "OrderItem", "Pricebook2", "PricebookEntry", "Product2", "ProductCategory",
                           "ProductCategoryProduct"],
    "territory.db": ["Territory2", "UserTerritory2Association"],
    "sales_pipeline.duckdb": ["Contract", "Lead", "Opportunity", "OpportunityLineItem", "Quote", "QuoteLineItem"],
    "activities.duckdb": ["Event", "Task", "VoiceCallTranscript__c"],
    "support": ["Case", "casehistory__c", "emailmessage", "issue__c", "knowledge__kav", "livechattranscript"],
}
RENAME = {"case": "support_case", "order": "sales_order", "user": "crm_user"}


def pg_name(t):
    t = t.lower()
    return RENAME.get(t, t)


def export(k, dab):
    """Write query_crmlive_cp<k>: the six original databases holding state k, for the raw setups."""
    src = dab / "query_crmarenapro" / "query_dataset"
    dst = dab / f"query_crmlive_cp{k}"
    qd = dst / "query_dataset"
    qd.mkdir(parents=True, exist_ok=True)
    cur = pg("crm").cursor()

    def rows_of(t):
        cur.execute(f'select * from "{pg_name(t)}"')
        cols = [d[0] for d in cur.description]
        return cols, cur.fetchall()

    for f in ("core_crm.db", "products_orders.db", "territory.db"):
        (qd / f).unlink(missing_ok=True)
        old, new = sqlite3.connect(src / f), sqlite3.connect(qd / f)
        for t in LAYOUT[f]:
            ddl = old.execute("select sql from sqlite_master where type='table' and name=?", (t,)).fetchone()[0]
            names = [r[1] for r in old.execute(f'pragma table_info("{t}")')]
            new.execute(ddl)
            cols, rows = rows_of(t)
            idx = [cols.index(n.lower()) for n in names]
            new.executemany(f'insert into "{t}" values ({",".join("?" * len(names))})', [tuple(r[i] for i in idx) for r in rows])
        new.commit()
    for f in ("sales_pipeline.duckdb", "activities.duckdb"):
        (qd / f).unlink(missing_ok=True)
        old, new = duckdb.connect(str(src / f), read_only=True), duckdb.connect(str(qd / f))
        for t in LAYOUT[f]:
            info = old.execute(f'describe "{t}"').fetchall()
            new.execute(f'create table "{t}" ({", ".join(chr(34) + n + chr(34) + " " + ty for n, ty, *_ in info)})')
            cols, rows = rows_of(t)
            idx = [cols.index(n.lower()) for n, *_ in info]
            if rows:
                new.executemany(f'insert into "{t}" values ({",".join("?" * len(info))})', [tuple(r[i] for i in idx) for r in rows])
        new.close()
    sup = f"crmlive_cp{k}_support"
    admin = pg("postgres").cursor()
    admin.execute(f"drop database if exists {sup} with (force)")
    admin.execute(f"create database {sup} template crm_orig_support")
    scur = pg(sup).cursor()
    for t in LAYOUT["support"]:
        cols, rows = rows_of(t)
        scur.execute(f'delete from "{t}"')
        if rows:
            execute_values(scur, f'insert into "{t}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) values %s', rows)
    cfg = (dab / "query_crmarenapro" / "db_config.yaml").read_text().replace("db_name: crm_support", f"db_name: {sup}")
    (dst / "db_config.yaml").write_text(cfg)
    for f in ("db_description.txt", "db_description_withhint.txt"):
        (dst / f).write_text((dab / "query_crmarenapro" / f).read_text())
    (qd / "support.sql").write_text("-- loaded directly by partb_crm.py export\n")



def digest(names, rows):
    """Order-independent content hash of a table: rows sorted by id, values as text."""
    import hashlib
    i = [n.lower() for n in names].index("id")
    norm = sorted(tuple("" if v is None else str(v) for v in r) for r in rows)
    norm.sort(key=lambda r: r[i])
    return len(norm), hashlib.sha256(repr(norm).encode()).hexdigest()


def verify_export(k, dab):
    """Every exported table holds exactly the live source's rows (count and content)."""
    qd = dab / f"query_crmlive_cp{k}" / "query_dataset"
    cur = pg("crm").cursor()
    bad = []

    def source(t):
        cur.execute(f'select * from "{pg_name(t)}"')
        return [d[0] for d in cur.description], cur.fetchall()

    def cmp(t, names, got):
        cols, rows = source(t)
        idx = [cols.index(n.lower()) for n in names]
        want = digest(names, [tuple(r[j] for j in idx) for r in rows])
        if digest(names, got) != want:
            bad.append(t)

    for f in ("core_crm.db", "products_orders.db", "territory.db"):
        c = sqlite3.connect(qd / f)
        for t in LAYOUT[f]:
            names = [r[1] for r in c.execute(f'pragma table_info("{t}")')]
            cmp(t, names, c.execute(f'select * from "{t}"').fetchall())
    for f in ("sales_pipeline.duckdb", "activities.duckdb"):
        c = duckdb.connect(str(qd / f), read_only=True)
        for t in LAYOUT[f]:
            names = [r[0] for r in c.execute(f'describe "{t}"').fetchall()]
            cmp(t, names, c.execute(f'select * from "{t}"').fetchall())
        c.close()
    scur = pg(f"crmlive_cp{k}_support").cursor()
    for t in LAYOUT["support"]:
        scur.execute(f'select * from "{t}"')
        cmp(t, [d[0] for d in scur.description], scur.fetchall())
    if bad:
        raise SystemExit(f"export of checkpoint {k} differs from the source in: {bad}")

if __name__ == "__main__":
    cmd, k = sys.argv[1], int(sys.argv[2])
    if cmd == "apply":
        print(json.dumps(apply(k)))
    elif cmd == "export":
        export(k, Path(sys.argv[3]))
    else:
        raise SystemExit(__doc__)
