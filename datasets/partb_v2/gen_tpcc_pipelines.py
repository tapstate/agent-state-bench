"""Generate the Tapstate workspace that consolidates a TPC-C database (Postgres `tpccsrc`) for Part B v2.

Views (one pipeline each, each on its own source listing only its tables):
  customer   one document per customer
  order      one document per order, its order lines embedded
  district   one document per district, its undelivered orders (new_order) embedded as the backlog
  stock      one document per (warehouse, item) stock record, the item embedded

TPC-C data is clean, so no cleanup rules: every step passes rows through, and every embed names its
key explicitly (a js step's output carries no primary key).

Usage: python gen_tpcc_pipelines.py <workspace-dir>
"""
import sys
from pathlib import Path

PASS = "function process(record, ctx) { return record; }"


def root_js(key):
    """Pass rows through and add `pk`, the composite key as one string: a view key is a single field."""
    parts = " + '-' + ".join(f"r.{k}" for k in key)
    return ("function process(record, ctx) { var r = record.after || record.before; "
            f"if (r != null) {{ r.pk = String({parts}); }} return record; }}")

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
    "stock": ("stock", ["s_w_id", "s_i_id"], [
        ("item", {"i_id": "s_i_id"}, "object", "item", ["i_id"]),
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
    for t in tables:
        y += (f"  - id: t_{t}\n    from: [ {t} ]\n    type: js\n    script: |\n"
              f"      {root_js(key) if t == root else PASS}\n")
    # The view is keyed by the single field `pk`, and a view key must be the identity of what feeds it;
    # a table's own identity is its composite primary key, so every view goes through a nest step whose
    # root is keyed by `pk` (with no embeds for a flat view).
    froms = ", ".join(f"t_{t}: t_{t}" for t in tables)
    y += (f"  - id: assemble\n    type: nest\n    from: {{ {froms} }}\n    root:\n      from: t_{root}\n"
          f"      key: [ pk ]\n")
    if embeds:
        y += "      embed:\n"
    for child, on, as_, path, ckey in embeds:
        on_s = ", ".join(f"{c}: {p}" for c, p in on.items())
        y += (f"        - from: t_{child}\n          on: {{ {on_s} }}\n          as: {as_}\n"
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
