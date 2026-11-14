"""The demo's "how the state is built" section, in three beats, as images and player segments:

1. the flow, drawn from the Tapstate workspace itself (dab/tapstate/): each source with its engine,
   database and product-key format, the staging pipelines that resolve keys, the four state pipelines
   and their live views, revealed column by column;
2. demo/product_state.example.tap.yml beside the BK-M68B-38 document from views.mfg_product, one
   embed line highlighted at a time next to the part of the document it produces;
3. the live change, rendered from recording/live_change.json (live_change.py's measured log): the WMS
   row on one side, the live document on the other, with the measured latency, then verify's result.

Simplified Chinese (zh) or English (en). Used by make_video.py.
"""
import asyncio
import html
import json
import re
from pathlib import Path

import yaml
from pymongo import MongoClient

from cdp import Chrome

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
WS = DEMO.parent / "dab" / "tapstate"
W, H = 1920, 1080 - 72          # the area above the player's caption bar

ENGINE_COLOR = {"postgres": "#58a6ff", "mysql": "#f0883e", "mongodb": "#3fb950"}
ENGINE_NAME = {"postgres": "Postgres", "mysql": "MySQL", "mongodb": "MongoDB"}
SYSTEMS = {  # source id -> (zh, en, key column = example value)
    "src_mfg_erp": ("ERP 销售", "ERP sales", "sku = BK-M68B-38"),
    "src_mfg_plm": ("PLM 产品主数据", "PLM", "product_number = BK-M68B-38"),
    "src_mfg_costing": ("成本核算", "Costing", "product_number = BK-M68B-38"),
    "src_mfg_mes": ("MES 生产执行", "MES", "part_no = bk-m68b-38"),
    "src_mfg_procurement": ("采购", "Procurement", "part_ref = BK_M68B_38"),
    "src_mfg_wms": ("WMS 仓储", "WMS", "item = AW:BK-M68B-38"),
}
STAGING = {  # pipeline -> (zh, en)
    "mfg_product_ref": ("每个产品在各系统中的编号格式", "each product's key in every system's format"),
    "mfg_stock": ("库存行 → 产品编号 + 库位", "stock rows → product number + location"),
    "mfg_offer": ("供应商报价 → 产品编号 + 供应商", "vendor offers → product number + vendor"),
    "mfg_routing": ("工艺工序 + 工作中心", "routing operations + work center"),
    "mfg_sales_line": ("销售订单行 + 产品", "sales order lines + product"),
    "mfg_po_line": ("采购订单行 + 产品", "purchase order lines + product"),
}
STATE = ["mfg_products", "mfg_work_orders", "mfg_sales_orders", "mfg_purchase_orders"]
COLLECTION = {"mfg_products": "products", "mfg_work_orders": "work_orders", "mfg_sales_orders": "sales_orders",
              "mfg_purchase_orders": "purchase_orders"}
TXT = {
    "zh": {"flow_title": "Tapstate 如何构建运营状态", "c1": "六个源系统（各自的产品编号格式）",
           "c2": "暂存管道：统一产品编号", "c3": "状态管道：嵌套（nest）", "c4": "实时状态",
           "arrow": "初始加载 + 每一次变更（CDC）",
           "foot": "由 dab/tapstate/ 中的 6 个数据源和 10 条管道定义自动生成；方案 C 的 mfg_state 是这些实时视图经逐字段校验后的副本。",
           "dsl_title": "声明式定义：一条管道产出一个产品文档", "left": "demo/product_state.example.tap.yml（精简版）",
           "right": "views.mfg_product 中的 BK-M68B-38（实际输出，已精简）",
           "live_title": "实时变更：改动源系统，状态随之更新", "src_panel": "源系统：WMS（MongoDB）mfg_wms.stock",
           "state_panel": "Tapstate 实时状态：views.mfg_product · BK-M68B-38", "inventory": "库存（inventory）",
           "waiting": "等待传播……", "updated": "已更新，用时 {s} 秒（经过 2 条管道：mfg_stock → mfg_products）",
           "verify_ok": "已改回原值；校验通过：实时视图与参考数据逐字段一致（校验用时 {s} 秒，视频中已略去等待）",
           "built_in": "自制产品，无外购报价"},
    "en": {"flow_title": "How Tapstate builds the operational state", "c1": "Six source systems (own key formats)",
           "c2": "Staging pipelines: one product key", "c3": "State pipelines: nest", "c4": "Live state",
           "arrow": "initial load + every change (CDC)",
           "foot": "Generated from the 6 sources and 10 pipelines in dab/tapstate/; setup C's mfg_state is a field-for-field verified copy of these live views.",
           "dsl_title": "Declarative: one pipeline, one product document", "left": "demo/product_state.example.tap.yml (simplified)",
           "right": "BK-M68B-38 in views.mfg_product (actual output, trimmed)",
           "live_title": "A live change: edit a source, the state follows", "src_panel": "Source: WMS (MongoDB) mfg_wms.stock",
           "state_panel": "Tapstate live state: views.mfg_product · BK-M68B-38", "inventory": "inventory",
           "waiting": "propagating…", "updated": "updated after {s} s (through 2 pipelines: mfg_stock → mfg_products)",
           "verify_ok": "Set back; verify passed: the live views equal the reference field for field (verify took {s} s, wait cut)",
           "built_in": "built in-house, no vendor offers"},
}

CSS = """
<style>
@font-face{font-family:"Noto Sans SC";src:url("../fonts/NotoSansSC-wght.ttf") format("truetype");font-weight:100 900}
html,body{margin:0;width:1920px;height:1008px;background:#0d1117;color:#e6edf3;overflow:hidden;
  font-family:"DejaVu Sans","Noto Sans SC",sans-serif}
.mono{font-family:"DejaVu Sans Mono","Noto Sans SC",monospace}
h1{font:700 40px "Noto Sans SC","DejaVu Sans",sans-serif;margin:34px 60px 18px}
.foot{position:absolute;left:60px;bottom:18px;font-size:20px;color:#8b949e}
</style>"""


def esc(s):
    return html.escape(str(s))


# --- beat 1: the flow ------------------------------------------------------------------------
def workspace_graph():
    src = {d["id"]: d for d in (yaml.safe_load(p.read_text()) for p in (WS / "source").glob("*.yml"))}
    pipes = {}
    for p in sorted((WS / "pipeline").glob("*.yml")):
        d = yaml.safe_load(p.read_text())
        body = p.read_text().split("transforms:", 1)[1]
        feeds = []
        for s in d["source"]:
            if s in ("src_mfg_stage", "src_mfg_ref"):   # pipelines' views read back: which ones
                feeds += [t for t in src[s]["tables"] if re.search(rf"\b{t}\b", body)]
            else:
                feeds.append(s)
        pipes[d["id"]] = {"feeds": feeds, "view": d["view"]["id"]}
    return src, pipes


def flow_page(lang, reveal):
    T = TXT[lang]
    src, pipes = workspace_graph()
    zi = 0 if lang == "zh" else 1
    cols = [60, 560, 1070, 1500]
    widths = [430, 440, 340, 360]
    boxes, y0 = {}, 175
    srcs = list(SYSTEMS)
    for i, s in enumerate(srcs):
        boxes[s] = (cols[0], y0 + i * 128, widths[0], 108)
    for i, p in enumerate(STAGING):
        boxes[p] = (cols[1], y0 + i * 128, widths[1], 108)
    for i, p in enumerate(STATE):
        boxes[p] = (cols[2], y0 + 60 + i * 168, widths[2], 120)
        boxes["v:" + p] = (cols[3], y0 + 60 + i * 168, widths[3], 120)
    # edges straight from the workspace: a source or a pipeline's view feeding a pipeline
    view_of = {v["view"]: k for k, v in pipes.items()}
    edges = []
    for p, d in pipes.items():
        for f in d["feeds"]:
            edges.append((view_of.get(f, f), p))
    for p in STATE:
        edges.append((p, "v:" + p))
    col_of = lambda n: 0 if n in SYSTEMS else 1 if n in STAGING else 2 if n in STATE else 3   # noqa: E731

    svg = []
    for a, b in edges:
        if a not in boxes or b not in boxes or col_of(b) > reveal:
            continue
        xa, ya, wa, ha = boxes[a]
        xb, yb, wb, hb = boxes[b]
        x1, y1, x2, y2 = xa + wa, ya + ha / 2, xb, yb + hb / 2
        color = ENGINE_COLOR.get(src[a]["connector"], "#6e7681") if a in src else "#6e7681"
        mx = (x1 + x2) / 2
        svg.append(f'<path d="M{x1},{y1} C{mx},{y1} {mx},{y2} {x2},{y2}" fill="none" stroke="{color}" '
                   f'stroke-width="2.5" opacity="0.75" marker-end="url(#ah)"/>')
    for n, (x, y, w, h) in boxes.items():
        c = col_of(n)
        if c > reveal:
            continue
        if n in SYSTEMS:
            eng = src[n]["connector"]
            db = src[n]["config"].get("database") or re.search(r"/([a-z_]+)\?", src[n]["config"]["uri"]).group(1)
            inner = (f'<div style="font:700 25px Noto Sans SC,DejaVu Sans">{esc(SYSTEMS[n][zi])}</div>'
                     f'<div style="color:{ENGINE_COLOR[eng]};font-size:20px">{ENGINE_NAME[eng]} · {esc(db)}</div>'
                     f'<div class="mono" style="font-size:19px;color:#d29922">{esc(SYSTEMS[n][2])}</div>')
            border = ENGINE_COLOR[eng]
        elif n in STAGING:
            inner = (f'<div class="mono" style="font-size:22px;font-weight:bold">{n}</div>'
                     f'<div style="font-size:20px;color:#c9d1d9">{esc(STAGING[n][zi])}</div>')
            border = "#8b949e"
        elif n in STATE:
            inner = (f'<div class="mono" style="font-size:23px;font-weight:bold">{n}</div>'
                     f'<div style="font-size:20px;color:#c9d1d9">nest → {esc(pipes[n]["view"])}</div>')
            border = "#39c5cf"
        else:
            p = n[2:]
            inner = (f'<div class="mono" style="font-size:22px">views.{pipes[p]["view"]}</div>'
                     f'<div class="mono" style="font-size:20px;color:#39c5cf">→ mfg_state.{COLLECTION[p]}</div>')
            border = "#39c5cf"
        svg.append(f'<foreignObject x="{x}" y="{y}" width="{w}" height="{h}"><div xmlns="http://www.w3.org/1999/xhtml" '
                   f'style="box-sizing:border-box;width:{w}px;height:{h}px;border:2px solid {border};border-radius:10px;'
                   f'background:#161b22;padding:10px 16px;line-height:1.35">{inner}</div></foreignObject>')
    heads = "".join(f'<text x="{cols[i]}" y="150" fill="#8b949e" font-size="22">{esc(T["c" + str(i + 1)])}</text>'
                    for i in range(reveal + 1))
    arrow = (f'<text x="{(cols[0] + widths[0] + cols[1]) / 2}" y="1000" fill="#8b949e" font-size="1"></text>')
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['flow_title'])}</h1>
<div style="position:absolute;right:60px;top:48px;font-size:26px;color:#d29922">⟶ {esc(T['arrow'])}</div>
<svg width="1920" height="1008" style="position:absolute;left:0;top:0">
<defs><marker id="ah" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#8b949e"/></marker></defs>
{heads}{''.join(svg)}{arrow}</svg><div class="foot">{esc(T['foot'])}</div></body>"""


# --- beat 2: the DSL beside the document -----------------------------------------------------------
EMBEDS = [  # (alias in the example, path in the real document, system zh, system en, engine)
    (None, "product", "PLM 产品主数据", "PLM", "postgres"),
    ("sc", "subcategory", "PLM 产品主数据", "PLM", "postgres"),
    ("ip", "list_price / standard_cost", "成本核算", "Costing", "postgres"),
    ("ph", "list_price_history", "成本核算", "Costing", "postgres"),
    ("bo", "bom", "PLM 产品主数据", "PLM", "postgres"),
    ("st", "inventory", "WMS 仓储（经 mfg_stock）", "WMS (through mfg_stock)", "mongodb"),
    ("vi", "vendors", "采购（经 mfg_offer）", "Procurement (through mfg_offer)", "mysql"),
]


def product_doc():
    import os
    v = MongoClient(os.environ.get("MONGO_URI", "mongodb://127.0.0.1:27017/?directConnection=true"))["views"]
    return v["mfg_product"].find_one({"product_number": "BK-M68B-38"})


def doc_lines(d, lang):
    day = lambda x: x.strftime("%Y-%m-%d") if x else "null"   # noqa: E731
    sc = d["subcategory"]
    L = [("product", f'product_number: "{d["product_number"]}"'), ("product", f'name: "{d["name"]}"'),
         ("product", f'make_flag: {int(d["make_flag"])}, safety_stock_level: {int(d["safety_stock_level"])}'),
         ("subcategory", f'subcategory: {{ name: "{sc["name"]}", category: {{ name: "{sc["category"]["name"]}" }} }}'),
         ("list_price / standard_cost", f'list_price: {d["list_price"]}, standard_cost: {d["standard_cost"]}'),
         ("list_price_history", "list_price_history: [")]
    for h in sorted(d["list_price_history"], key=lambda h: h["start_date"]):
        L.append(("list_price_history", f'  {{ start_date: {day(h["start_date"])}, end_date: {day(h["end_date"])}, list_price: {h["list_price"]} }}'))
    L.append(("list_price_history", "]"))
    L.append(("bom", f'bom: [  // {len(d["bom"])}'))
    for b in sorted(d["bom"], key=lambda b: b["component_number"])[:3]:
        L.append(("bom", f'  {{ component_number: "{b["component_number"]}", per_assembly_qty: {b["per_assembly_qty"]} }}'))
    L.append(("bom", "  …  ]"))
    L.append(("inventory", "inventory: ["))
    for i in sorted(d["inventory"], key=lambda i: int(i["location"]["location_id"])):
        L.append(("inventory", f'  {{ location: {{ name: "{i["location"]["name"]}" }}, quantity: {int(i["quantity"])} }}'))
    L.append(("inventory", "]"))
    L.append(("vendors", f'vendors: []   // {TXT[lang]["built_in"]}'))
    return L


def dsl_page(lang, step):
    T = TXT[lang]
    alias, path, szh, sen, eng = EMBEDS[step]
    code = (DEMO / "product_state.example.tap.yml").read_text().splitlines()
    code = [ln for ln in code if not ln.startswith("#")]   # the file's long comment is the README's job
    rows = []
    for ln in code:
        hit = (alias and re.search(rf"\{{ from: {alias},", ln)) or (alias is None and re.search(r"from: p\b|key: \[ product_number \]|id: product_state", ln))
        style = f"background:#1f6feb44;border-left:4px solid {ENGINE_COLOR[eng]}" if hit else "border-left:4px solid transparent"
        short = re.sub(r",\s*(key|arrayKey): \[[^\]]*\]", "", ln)    # keys stay in the file; trimmed for width
        short = re.sub(r"\s+#.*$", "", short)
        rows.append(f'<div class="mono" style="font-size:16.5px;line-height:1.5;white-space:pre;padding:1px 8px;{style}">{esc(short)}</div>')
    d = product_doc()
    drows = []
    for part, ln in doc_lines(d, lang):
        hit = part == path
        style = f"background:#1f6feb44;border-left:4px solid {ENGINE_COLOR[eng]}" if hit else "border-left:4px solid transparent;opacity:.55"
        drows.append(f'<div class="mono" style="font-size:16.5px;line-height:1.5;white-space:pre;padding:1px 8px;{style}">{esc(ln)}</div>')
    label = szh if lang == "zh" else sen
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['dsl_title'])}</h1>
<div style="position:absolute;left:40px;top:110px;width:1020px;font-size:20px;color:#8b949e">{esc(T['left'])}</div>
<div style="position:absolute;left:40px;top:145px;width:1020px;height:800px;overflow:hidden">{''.join(rows)}</div>
<div style="position:absolute;left:1090px;top:110px;width:800px;font-size:20px;color:#8b949e">{esc(T['right'])}</div>
<div style="position:absolute;left:1090px;top:145px;width:800px;height:740px;overflow:hidden">{''.join(drows)}</div>
<div style="position:absolute;left:1090px;top:895px;font-size:28px;font-weight:700;color:{ENGINE_COLOR[eng]}">
{esc(path)} ← {esc(label)} · {ENGINE_NAME[eng]}</div></body>"""


# --- beat 3: the live change ----------------------------------------------------------------------
def live_page(lang, ev, phase, latency=None, verify=None):
    T = TXT[lang]
    s = ev["source"]
    changed_src = phase in ("set", "seen")
    doc = (f'{{\n  _id: {s["_id"]},\n  item: "{s["item"]}",\n  location_id: {s["location_id"]},\n'
           f'  quantity: <span style="color:{"#f0883e" if changed_src else "#e6edf3"};font-weight:bold">{s["quantity"]}</span>\n}}')
    cmd = f'db.stock.updateOne({{ _id: {s["_id"]} }}, {{ $set: {{ quantity: {s["quantity"]} }} }})' if phase != "start" else ""
    inv = "".join(
        f'<div class="mono" style="font-size:26px;padding:6px 0">{esc(i["location"]):<24} quantity '
        f'<span style="color:{"#3fb950" if (phase == "seen" and i["location"] == "Final Assembly") else "#e6edf3"};'
        f'font-weight:bold">{i["quantity"]}</span></div>' for i in ev["inventory"])
    status = ""
    if phase == "set":
        status = f'<span style="color:#d29922">{esc(T["waiting"])}</span>'
    elif phase == "seen":
        status = f'<span style="color:#3fb950">{esc(T["updated"].format(s=f"{latency:.2f}"))}</span>'
    elif phase == "verify":
        status = f'<span style="color:#3fb950">{esc(T["verify_ok"].format(s=verify["seconds"]))}</span>'
    verify_line = f'<div class="mono" style="font-size:20px;color:#8b949e;margin-top:14px">$ python verify_tapstate.py<br>{esc(verify["line"])}</div>' if verify else ""
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['live_title'])}</h1>
<div style="position:absolute;left:60px;top:120px;width:860px;height:600px;border:2px solid #3fb950;border-radius:12px;background:#161b22;padding:24px;box-sizing:border-box">
 <div style="font-size:24px;color:#3fb950;margin-bottom:16px">{esc(T['src_panel'])}</div>
 <div class="mono" style="font-size:20px;color:#39c5cf;min-height:56px">{('$ ' + esc(cmd)) if cmd else ''}</div>
 <pre class="mono" style="font-size:26px;margin:16px 0">{doc}</pre></div>
<div style="position:absolute;left:1000px;top:120px;width:860px;height:600px;border:2px solid #39c5cf;border-radius:12px;background:#161b22;padding:24px;box-sizing:border-box">
 <div style="font-size:24px;color:#39c5cf;margin-bottom:16px">{esc(T['state_panel'])}</div>
 <div style="font-size:22px;color:#8b949e;margin:10px 0 8px">{esc(T['inventory'])}</div>{inv}</div>
<div style="position:absolute;left:60px;top:750px;width:1800px;font-size:28px">{status}{verify_line}</div></body>"""


# --- rendering ---------------------------------------------------------------------------------
def deferred(page):
    """The page with its body inserted only once Noto Sans SC is loaded. Text laid out before the
    font arrives is shaped with fallback fonts (boxes, with no CJK font installed), and Chrome keeps
    that shaping for the string even after the font loads; with nothing laid out early, that cannot
    happen."""
    head, body = page.split("<body>", 1)
    body = body.replace("</body>", "")
    return (head + '<body><template id="content">' + body + '</template><script>'
            'Promise.all([document.fonts.load(\'20px "Noto Sans SC"\'), document.fonts.load(\'bold 20px "Noto Sans SC"\')])'
            '.then(() => { document.body.appendChild(document.getElementById("content").content.cloneNode(true));'
            ' return document.fonts.ready; }).then(() => { window.ready = true; });</script></body>')


# every character on the page, SVG text included, in both weights (Google Fonts serves CJK in subsets)
FONT_LOAD = """(async () => { const t = document.documentElement.textContent;
  for (const w of ['', 'bold ']) await document.fonts.load(w + '20px "Noto Sans SC"', t);
  await document.fonts.ready; return [...document.fonts].filter(f => f.status === 'error').length; })()"""


async def shoot(pages, out_dir, port=9340):
    out_dir.mkdir(exist_ok=True)
    async with Chrome(width=W, height=H, port=port) as c:
        for name, page in pages:
            f = out_dir / f"{name}.html"
            f.write_text(deferred(page))
            await c.goto(f.as_uri(), settle=0.1)
            for _ in range(200):
                if await c.eval("window.ready === true"):
                    break
                await asyncio.sleep(0.05)
            else:
                raise RuntimeError(f"{name}: the font did not load")
            errors = await c.eval(FONT_LOAD, await_promise=True)
            if errors:
                raise RuntimeError(f"{name}: {errors} font faces failed to load")
            await asyncio.sleep(0.3)
            await c.screenshot(out_dir / f"{name}.png")


def section(lang, captions):
    """The three beats as player segments ("page" kind, still images switched at key times)."""
    out = HERE / f"build_{lang}"
    pages = [(f"flow_{r}", flow_page(lang, r)) for r in range(4)]
    pages += [(f"dsl_{i}", dsl_page(lang, i)) for i in range(len(EMBEDS))]
    log = json.loads((HERE / "live_change.json").read_text())
    ev = log["events"]   # start, set, seen, set, seen
    steps = log["steps"]
    pages += [("live_0", live_page(lang, ev[0], "start")),
              ("live_1", live_page(lang, ev[1], "set")),
              ("live_2", live_page(lang, ev[2], "seen", steps[0]["latency_s"])),
              ("live_3", live_page(lang, ev[3], "set")),
              ("live_4", live_page(lang, ev[4], "seen", steps[1]["latency_s"])),
              ("live_5", live_page(lang, ev[4], "verify", verify=log["verify"]))]
    asyncio.run(shoot(pages, out))
    imgs = {n: f"build_{lang}/{n}.png" for n, _ in pages}

    def still(keys, dur, caption):
        return {"kind": "page", "dur": dur, "caption": caption, "keys": [[t, im, 0] for t, im in keys]}
    flow = still([(0, "flow_0"), (3, "flow_1"), (6.5, "flow_2"), (10, "flow_3")], 16, captions[0])
    flow["still_at"] = 14
    dsl = still([(i * 3.0, f"dsl_{i}") for i in range(len(EMBEDS))], len(EMBEDS) * 3.0 + 1, captions[1])
    dsl["still_at"] = 5 * 3.0 + 1     # the inventory line (WMS)
    # the live change at its measured times: start, set, seen (+latency), hold, set back, seen, verify
    l0, l1 = steps[0]["latency_s"], steps[1]["latency_s"]
    t_set1, t_set2 = 4.0, 14.0
    live = still([(0, "live_0"), (t_set1, "live_1"), (t_set1 + l0, "live_2"), (t_set2, "live_3"),
                  (t_set2 + l1, "live_4"), (t_set2 + 6, "live_5")], t_set2 + 16, captions[2])
    live["still_at"] = t_set1 + l0 + 2     # just after the change arrived
    return [flow, dsl, live], imgs, log
