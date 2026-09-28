"""Generate the Tapstate workspace that consolidates a TPC-C database (Postgres `tpccsrc`) for Part B v2.

Views (one pipeline each, each on its own source listing only its tables):
  customer   one document per customer
  order      one document per order, its order lines embedded
  district   one document per district, its undelivered orders (new_order) embedded as the backlog
  item       one document per item, its stock record in each warehouse embedded

TPC-C data is clean, so no cleanup rules: every step passes rows through, and every embed names its
key explicitly (a js step's output carries no primary key).

Usage: python gen_tpcc_pipelines.py <workspace-dir>
"""
import sys
from pathlib import Path

PASS = "function process(record, ctx) { return record; }"


def root_js(cols, field="pk"):
    """Pass rows through and add `field`, the given columns joined into one string: a view key is a single
    field, and an embed joins on its parent's key."""
    parts = " + '-' + ".join(f"r.{k}" for k in cols)
    return ("function process(record, ctx) { var r = record.after || record.before; "
            f"if (r != null) {{ r.{field} = String({parts}); }} return record; }}")

# view -> (root table, root key, [(child table, on {child col: parent col}, as, path, key)])
VIEWS = {
    "customer": ("customer", ["c_w_id", "c_d_id", "c_id"], []),
    "order": ("orders", ["o_w_id", "o_d_id", "o_id"], [
        ("order_line", {"ol_w_id": "o_w_id", "ol_d_id": "o_d_id", "ol_o_id": "o_id"}, "array", "lines",
         ["ol_number"]),
    ]),
    "district": ("district", ["d_w_id", "d_id"], [
        ("new_order", {"no_w_id": "d_w_id", "no_d_id": "d_id"}, "array", "backlog", ["no_o_id"]),
    ]),
    "item": ("item", ["i_id"], [
        ("stock", {"s_i_id": "i_id"}, "array", "stock", ["s_w_id"]),
    ]),
}
SOURCE = ("config: { host: postgres, port: 5432, database: tpccsrc, schema: public, user: postgres, "
          "password: secret }\n")


def source_yaml(sid, tables):
    return (f"version: tapstate/v1\nkind: source\nid: {sid}\nconnector: postgres\n{SOURCE}"
            f"mode: cdc\ntables: [ {', '.join(tables)} ]\n")


def pipeline_yaml(view, root, key, embeds):
    tables = [root] + [e[0] for e in embeds]
    y = (f"version: tapstate/v1\nkind: pipeline\nid: {view}_state\nsource: [ tpcc_{view} ]\n"
         f"settings: {{ read_mode: snapshot_and_cdc }}\ntransforms:\n")
    # An embed joins on its parent's key, which is `pk`; each child derives the same string from its
    # own foreign-key columns, in the parent key's order, as `ppk`.
    child_keys = {c: [next(k for k, v in on.items() if v == pcol) for pcol in key] for c, on, _, _, _ in embeds}
    for t in tables:
        js = root_js(key) if t == root else (root_js(child_keys[t], "ppk") if t in child_keys else PASS)
        y += f"  - id: t_{t}\n    from: [ {t} ]\n    type: js\n    script: |\n      {js}\n"
    # The view is keyed by the single field `pk`, and a view key must be the identity of what feeds it;
    # a table's own identity is its composite primary key, so every view goes through a nest step whose
    # root is keyed by `pk` (with no embeds for a flat view).
    froms = ", ".join(f"t_{t}: t_{t}" for t in tables)
    y += (f"  - id: assemble\n    type: nest\n    from: {{ {froms} }}\n    root:\n      from: t_{root}\n"
          f"      key: [ pk ]\n")
    if embeds:
        y += "      embed:\n"
    for child, on, as_, path, ckey in embeds:
        y += (f"        - from: t_{child}\n          on: {{ ppk: pk }}\n          as: {as_}\n"
              f"          key: [ {', '.join(ckey)} ]\n          path: {path}\n")
        if as_ == "array":
            y += f"          arrayKey: [ {', '.join(ckey)} ]\n"
    return y + f"view: {{ id: {view}, from: assemble, primary_key: pk }}\n"


def main():
    ws = Path(sys.argv[1])
    for d in ("source", "pipeline"):
        (ws / d).mkdir(parents=True, exist_ok=True)
        for old in (ws / d).glob("*.tap.yml"):
            old.unlink()
    for view, (root, key, embeds) in VIEWS.items():
        tables = [root] + [e[0] for e in embeds]
        (ws / "source" / f"tpcc_{view}.tap.yml").write_text(source_yaml(f"tpcc_{view}", tables))
        (ws / "pipeline" / f"{view}_state.tap.yml").write_text(pipeline_yaml(view, root, key, embeds))
    print(f"{len(VIEWS)} pipelines -> {ws}")


if __name__ == "__main__":
    main()
