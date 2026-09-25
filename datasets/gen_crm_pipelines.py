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


def pipeline_yaml(view, root, embeds):
    tables = tables_of(root, embeds)
    y = (f"version: tapstate/v1\nkind: pipeline\nid: {view}_state\nsource: [ crm ]\n"
         f"settings: {{ read_mode: snapshot_and_cdc }}\ntransforms:\n")
    for t in tables:
        y += f"  - id: {alias(t)}\n    from: [ {t} ]\n    type: js\n    script: |\n"
        y += indent(CLEAN_JS, 6)
    if embeds:
        froms = ", ".join(f"{alias(t)}: {alias(t)}" for t in tables)
        y += (f"  - id: assemble\n    type: nest\n    from: {{ {froms} }}\n"
              f"    root:\n      from: {alias(root)}\n      key: [ id ]\n")
        y += embed_yaml(embeds, 6)
        y += f"view: {{ id: {view}, from: assemble, primary_key: id }}\n"
    else:
        y += f"view: {{ id: {view}, from: {alias(root)}, primary_key: id }}\n"
    return y


def main():
    ws = Path(sys.argv[1])
    (ws / "source").mkdir(parents=True, exist_ok=True)
    (ws / "pipeline").mkdir(parents=True, exist_ok=True)
    for old in (ws / "pipeline").glob("*.tap.yml"):
        old.unlink()
    (ws / "source" / "crm.tap.yml").write_text(
        "version: tapstate/v1\nkind: source\nid: crm\nconnector: postgres\n"
        "config: { host: postgres, port: 5432, database: crm, schema: public, user: postgres, password: secret }\n"
        f"mode: cdc\ntables: [ {', '.join(ALL_TABLES)} ]\n")
    for view, (root, embeds) in VIEWS.items():
        (ws / "pipeline" / f"{view}_state.tap.yml").write_text(pipeline_yaml(view, root, embeds))
    print(f"{len(VIEWS)} pipelines over {len(ALL_TABLES)} tables -> {ws}")


if __name__ == "__main__":
    main()
