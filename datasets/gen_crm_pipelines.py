"""Generate the Tapstate workspace that consolidates DAB crmarenapro into MongoDB entity documents.

One pipeline per view (a pipeline declares one view). Every table passes through its own `js`
step that applies the only cleanup rule used: trim every string, and strip a leading '#' from
id-like fields (`id`, `*id`, `*id__c`). The rule is generic identifier normalization, the kind a
customer pipeline would carry; nothing in it names a DAB table, value or query.

Documents are current state only (no history), keyed by the cleaned `id`.

Usage: python gen_crm_pipelines.py <workspace-dir>
"""
import sys
from pathlib import Path

CLEAN_JS = """\
function fix(row) {
  if (row == null) return row;
  for (var k in row) {
    var v = row[k];
    if (typeof v === 'string') {
      v = v.trim();
      if (k === 'id' || /id$|id__c$/.test(k)) v = v.replace(/^#/, '');
      row[k] = v;
    }
  }
  return row;
}
function process(record, ctx) { fix(record.after); fix(record.before); return record; }
"""

# A Salesforce id starts with a 3-character key prefix naming its object type.
KEY_PREFIX = {"support_case": "500", "opportunity": "006", "lead": "00Q", "account": "001", "quote": "0Q0",
              "sales_order": "801", "crm_user": "005"}


def link_filter_js(fk, prefix):
    """Drop a child row whose link to the root is empty or names another object type: it can never
    join, and a child that arrives by CDC with no parent stalls the v0.5.0 nest step."""
    return (f"function filter(record) {{\n  var r = record.after || record.before;\n"
            f"  var v = r == null ? '' : String(r['{fk}'] == null ? '' : r['{fk}']).trim().replace(/^#/, '');\n"
            f"  return v.indexOf('{prefix}') === 0;\n}}\n")


# view id -> (root table, [embed]); embed = (table, parent-key column in child, path, [nested embeds])
VIEWS = {
    "account": ("account", [
        ("contact", "accountid", "contacts", []),
        ("contract", "accountid", "contracts", []),
    ]),
    "opportunity": ("opportunity", [
        ("opportunitylineitem", "opportunityid", "line_items", []),
        ("quote", "opportunityid", "quotes", [
            ("quotelineitem", "quoteid", "line_items", []),
        ]),
        ("task", "whatid", "tasks", []),
        ("event", "whatid", "events", []),
        ("voicecalltranscript__c", "opportunityid__c", "voice_calls", []),
        ("emailmessage", "relatedtoid", "emails", []),
    ]),
    "lead": ("lead", [
        ("voicecalltranscript__c", "leadid__c", "voice_calls", []),
    ]),
    "support_case": ("support_case", [
        ("casehistory__c", "caseid__c", "history", []),
        ("emailmessage", "parentid", "emails", []),
        ("livechattranscript", "caseid", "chats", []),
    ]),
    "quote": ("quote", [
        ("quotelineitem", "quoteid", "line_items", []),
    ]),
    "sales_order": ("sales_order", [
        ("orderitem", "orderid", "items", []),
    ]),
    "product": ("product2", [
        ("pricebookentry", "product2id", "pricebook_entries", []),
        ("productcategoryproduct", "productid", "categories", []),
    ]),
    "crm_user": ("crm_user", [
        ("userterritory2association", "userid", "territories", []),
    ]),
    # Standalone entities: cleaned, one document per row.
    "contact": ("contact", []),
    "contract": ("contract", []),
    "knowledge_article": ("knowledge__kav", []),
    "issue": ("issue__c", []),
    "territory": ("territory2", []),
    "product_category": ("productcategory", []),
    "pricebook": ("pricebook2", []),
}

ALL_TABLES = sorted({t for root, emb in VIEWS.values() for t in [root] + [
    x for e in emb for x in [e[0]] + [n[0] for n in e[3]]]})


def indent(text, n):
    pad = " " * n
    return "".join(pad + line + "\n" if line else "\n" for line in text.splitlines())


def tables_of(root, embeds):
    out = [root]
    for table, _, _, nested in embeds:
        out.append(table)
        out.extend(n[0] for n in nested)
    return list(dict.fromkeys(out))


def embed_yaml(embeds, depth):
    pad = " " * depth
    y = f"{pad}embed:\n"
    for table, fk, path, nested in embeds:
        y += (f"{pad}  - from: {alias(table)}\n{pad}    on: {{ {fk}: id }}\n"
              f"{pad}    as: array\n{pad}    path: {path}\n{pad}    arrayKey: [ id ]\n")
        if nested:
            y += embed_yaml(nested, depth + 4)
    return y


def alias(table):
    return "t_" + table.replace("__", "_")


def pipeline_yaml(view, root, embeds, source="crm", link_filter=False):
    tables = tables_of(root, embeds)
    filters = {t: link_filter_js(fk, KEY_PREFIX[root]) for t, fk, _, _ in embeds} if link_filter else {}
    y = (f"version: tapstate/v1\nkind: pipeline\nid: {view}_state\nsource: [ {source} ]\n"
         f"settings: {{ read_mode: snapshot_and_cdc }}\ntransforms:\n")
    for t in tables:
        y += f"  - id: {alias(t)}\n    from: [ {t} ]\n    type: js\n    script: |\n"
        y += indent(CLEAN_JS + filters.get(t, ""), 6)
    if embeds:
        froms = ", ".join(f"{alias(t)}: {alias(t)}" for t in tables)
        y += (f"  - id: assemble\n    type: nest\n    from: {{ {froms} }}\n"
              f"    root:\n      from: {alias(root)}\n      key: [ id ]\n")
        y += embed_yaml(embeds, 6)
        y += f"view: {{ id: {view}, from: assemble, primary_key: id }}\n"
    else:
        y += f"view: {{ id: {view}, from: {alias(root)}, primary_key: id }}\n"
    return y


PARTB_VIEWS = ["support_case", "crm_user", "lead", "opportunity", "account", "quote", "sales_order"]
SOURCE_CONFIG = ("config: { host: postgres, port: 5432, database: crm, schema: public, user: postgres, "
                 "password: secret }\n")


def main():
    """Usage: gen_crm_pipelines.py <workspace> [--partb]

    --partb: only the views the Part B questions read, each pipeline on its own source listing only
    its tables. A pipeline reads every table of its source, so one shared 27-table source makes
    every pipeline decode every table's changes; with many pipelines catching up at once that ran
    the v0.5.0 server out of memory. Part B pipelines also drop child rows that cannot join their
    root (see link_filter_js): replayed changes include such rows, and they stall the nest step.
    """
    ws = Path(sys.argv[1])
    partb = "--partb" in sys.argv[2:]
    (ws / "source").mkdir(parents=True, exist_ok=True)
    (ws / "pipeline").mkdir(parents=True, exist_ok=True)
    for old in list((ws / "pipeline").glob("*.tap.yml")) + list((ws / "source").glob("*.tap.yml")):
        old.unlink()
    views = {v: VIEWS[v] for v in PARTB_VIEWS} if partb else VIEWS
    if not partb:
        (ws / "source" / "crm.tap.yml").write_text(
            "version: tapstate/v1\nkind: source\nid: crm\nconnector: postgres\n" + SOURCE_CONFIG
            + f"mode: cdc\ntables: [ {', '.join(ALL_TABLES)} ]\n")
    for view, (root, embeds) in views.items():
        source = f"crm_{view}" if partb else "crm"
        if partb:
            (ws / "source" / f"{source}.tap.yml").write_text(
                f"version: tapstate/v1\nkind: source\nid: {source}\nconnector: postgres\n" + SOURCE_CONFIG
                + f"mode: cdc\ntables: [ {', '.join(tables_of(root, embeds))} ]\n")
        (ws / "pipeline" / f"{view}_state.tap.yml").write_text(pipeline_yaml(view, root, embeds, source, link_filter=partb))
    print(f"{len(views)} pipelines -> {ws}")

if __name__ == "__main__":
    main()
