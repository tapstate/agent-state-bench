"""The demo's "how the state is built" section, in three beats, as images and player segments:

1. the flow, drawn from the Tapstate workspace itself (dab/tapstate/): one straight lane per source
   (engine, database, product-key format, then the staging pipelines that read it), converging only
   into the four state (nest) pipelines and their live views, revealed column by column;
2. demo/product_state.example.tap.yml beside the BK-M68B-38 document from views.mfg_product, one
   embed line highlighted at a time next to the part of the document it produces;
3. the live change, rendered from recording/live_change.json (live_change.py's measured log): the source
   row and its update on one side (by default costing's list price in Postgres), the live document on
   the other, with the measured latency, then verify's result.

Simplified Chinese (zh) or English (en). Used by make_video.py.
"""
import asyncio
import html
import json
import os
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
# "08": the Tapstate 0.8.0 take (make_video.py --v3): its own images and live_change08.json
VARIANT = os.environ.get("DEMO_VARIANT", "")

TXT = {
    "zh": {"flow_title": "Tapstate 如何构建运营状态", "c1": "六个源系统（各自的产品编号格式）",
           "c2": "暂存管道：统一产品编号", "c3": "状态管道：嵌套（nest）", "c4": "实时状态",
           "arrow": "初始加载 + 每一次变更（CDC）",
           "foot": "由 dab/tapstate/ 中的 6 个数据源和 10 条管道定义自动生成；方案 C 的 mfg_state 是这些实时视图经逐字段校验后的副本。",
           "foot2": "产品编号格式经 mfg_product_ref 统一解析（订单行、工单在暂存时查找它）。",
           "direct": "无需暂存：编号已是标准格式，直接进入嵌套",
           "dsl_title": "声明式定义：一条管道产出一个产品文档", "left": "demo/product_state.example.tap.yml（精简版）",
           "right": "views.mfg_product 中的 BK-M68B-38（实际输出，已精简）",
           "live_title": "实时变更：改动源系统，状态随之更新", "src_panel": "源系统：{system}（{engine}）{table}",
           "state_panel": "Tapstate 实时状态：views.mfg_product · BK-M68B-38", "inventory": "库存（inventory）",
           "waiting": "等待传播……", "updated": "已更新，用时 {s} 秒（经过 {n} 条管道：{pipes}）", "pipes_sep": " → ",
           "systems": {"costing": "成本核算", "wms": "WMS 仓储"},
           "verify_ok": "已改回原值；校验通过：实时视图与参考数据逐字段一致（校验用时 {s} 秒，视频中已略去等待）",
           "built_in": "自制产品，无外购报价"},
    "en": {"flow_title": "How Tapstate builds the operational state", "c1": "Six source systems (own key formats)",
           "c2": "Staging pipelines: one product key", "c3": "State pipelines: nest", "c4": "Live state",
           "arrow": "initial load + every change (CDC)",
           "foot": "Generated from the 6 sources and 10 pipelines in dab/tapstate/; setup C's mfg_state is a field-for-field verified copy of these live views.",
           "foot2": "Key formats resolved via mfg_product_ref (order lines and work orders look it up while staging).",
           "direct": "no staging: the key is already canonical; read by the nest directly",
           "dsl_title": "Declarative: one pipeline, one product document", "left": "demo/product_state.example.tap.yml (simplified)",
           "right": "BK-M68B-38 in views.mfg_product (actual output, trimmed)",
           "live_title": "A live change: edit a source, the state follows", "src_panel": "Source: {system} ({engine}) {table}",
           "state_panel": "Tapstate live state: views.mfg_product · BK-M68B-38", "inventory": "inventory",
           "waiting": "propagating…", "updated": "updated after {s} s (through {n} pipeline(s): {pipes})", "pipes_sep": " → ",
           "systems": {"costing": "Costing", "wms": "WMS"},
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

if VARIANT == "08":   # say which Tapstate the section shows
    TXT["zh"]["flow_title"] = "Tapstate 0.8.0 如何构建运营状态"
    TXT["en"]["flow_title"] = "How Tapstate 0.8.0 builds the operational state"
    TXT["zh"]["live_title"] += "（Tapstate 0.8.0）"
    TXT["en"]["live_title"] += " (Tapstate 0.8.0)"

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


def lanes():
    """One lane per source system, straight from the workspace: the system's source, the staging
    pipelines that read it (their views are read back by src_mfg_stage/src_mfg_ref), and the state
    pipelines the lane feeds (directly, or through its staging views). mfg_product_ref is the shared key
    lookup; it sits in the PLM lane, and the lookups from other lanes are a footnote, not arrows."""
    src, pipes = workspace_graph()
    staging = {p for p in pipes if p not in STATE}
    out = []
    for s_id in SYSTEMS:
        stage = [p for p in STAGING if p in staging and s_id in pipes[p]["feeds"]]
        views = {pipes[p]["view"] for p in stage}
        feeds = [p for p in STATE if s_id in pipes[p]["feeds"] or views & set(pipes[p]["feeds"])]
        out.append({"source": s_id, "staging": stage, "feeds": feeds})
    return src, pipes, out


def flow_page(lang, reveal):
    T = TXT[lang]
    src, pipes, lane_list = lanes()
    zi = 0 if lang == "zh" else 1
    X = [60, 560, 1090, 1500]
    Wd = [430, 440, 330, 360]
    top, lane_h, gap = 178, 100, 18
    svg = []

    def box(x, y, w, h, border, inner, dashed=False):
        style = "dashed" if dashed else "solid"
        return (f'<foreignObject x="{x}" y="{y}" width="{w}" height="{h}"><div xmlns="http://www.w3.org/1999/xhtml" '
                f'style="box-sizing:border-box;width:{w}px;height:{h}px;border:2px {style} {border};border-radius:10px;'
                f'background:#161b22;padding:9px 16px;line-height:1.35">{inner}</div></foreignObject>')
    lane_y = {}
    for i, ln in enumerate(lane_list):
        y = top + i * (lane_h + gap)
        lane_y[ln["source"]] = y
        s_id = ln["source"]
        eng = src[s_id]["connector"]
        color = ENGINE_COLOR[eng]
        db = src[s_id]["config"].get("database") or re.search(r"/([a-z_]+)\?", src[s_id]["config"]["uri"]).group(1)
        svg.append(box(X[0], y, Wd[0], lane_h, color,
                       f'<div style="font:700 25px Noto Sans SC,DejaVu Sans">{esc(SYSTEMS[s_id][zi])}</div>'
                       f'<div style="color:{color};font-size:20px">{ENGINE_NAME[eng]} · {esc(db)}</div>'
                       f'<div class="mono" style="font-size:19px;color:#d29922">{esc(SYSTEMS[s_id][2])}</div>'))
        if reveal >= 1:
            ym = y + lane_h / 2
            svg.append(f'<line x1="{X[0] + Wd[0]}" y1="{ym}" x2="{X[1]}" y2="{ym}" stroke="{color}" stroke-width="3" marker-end="url(#ah)"/>')
            if ln["staging"]:
                inner = "".join(f'<div style="font-size:20px"><span class="mono" style="font-weight:bold">{p}</span>'
                                f'<span style="color:#c9d1d9">　{esc(STAGING[p][zi])}</span></div>' for p in ln["staging"])
                svg.append(box(X[1], y, Wd[1], lane_h, "#8b949e", f'<div style="display:flex;flex-direction:column;justify-content:center;height:100%">{inner}</div>'))
            else:
                svg.append(box(X[1], y, Wd[1], lane_h, "#6e7681",
                               f'<div style="display:flex;align-items:center;height:100%;font-size:20px;color:#8b949e">{esc(T["direct"])}</div>',
                               dashed=True))
    nest_y = {}
    for i, p in enumerate(STATE):
        nest_y[p] = top + 20 + i * 178
    if reveal >= 2:
        for ln in lane_list:   # the only converging arrows: lanes into the nest pipelines
            ya = lane_y[ln["source"]] + lane_h / 2
            color = ENGINE_COLOR[src[ln["source"]]["connector"]]
            for p in ln["feeds"]:
                yb = nest_y[p] + 60
                mx = (X[1] + Wd[1] + X[2]) / 2
                svg.append(f'<path d="M{X[1] + Wd[1]},{ya} C{mx},{ya} {mx},{yb} {X[2]},{yb}" fill="none" stroke="{color}" '
                           f'stroke-width="2.5" opacity="0.8" marker-end="url(#ah)"/>')
        for p in STATE:
            svg.append(box(X[2], nest_y[p], Wd[2], 120, "#39c5cf",
                           f'<div class="mono" style="font-size:23px;font-weight:bold">{p}</div>'
                           f'<div style="font-size:20px;color:#c9d1d9">nest → {esc(pipes[p]["view"])}</div>'))
    if reveal >= 3:
        for p in STATE:
            y = nest_y[p]
            svg.append(f'<line x1="{X[2] + Wd[2]}" y1="{y + 60}" x2="{X[3]}" y2="{y + 60}" stroke="#39c5cf" stroke-width="2.5" marker-end="url(#ah)"/>')
            svg.append(box(X[3], y, Wd[3], 120, "#39c5cf",
                           f'<div class="mono" style="font-size:22px">views.{pipes[p]["view"]}</div>'
                           f'<div class="mono" style="font-size:20px;color:#39c5cf">→ mfg_state.{COLLECTION[p]}</div>'))
    heads = "".join(f'<text x="{X[i]}" y="155" fill="#8b949e" font-size="22">{esc(T["c" + str(i + 1)])}</text>'
                    for i in range(reveal + 1))
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['flow_title'])}</h1>
<div style="position:absolute;right:60px;top:48px;font-size:26px;color:#d29922">⟶ {esc(T['arrow'])}</div>
<svg width="1920" height="1008" style="position:absolute;left:0;top:0">
<defs><marker id="ah" markerWidth="10" markerHeight="8" refX="9" refY="4" orient="auto"><path d="M0,0 L10,4 L0,8 z" fill="#8b949e"/></marker></defs>
{heads}{''.join(svg)}</svg>
<div class="foot" style="bottom:46px">{esc(T['foot2'])}</div><div class="foot">{esc(T['foot'])}</div></body>"""


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
def live_page(lang, log, ev, phase, latency=None, verify=None):
    T = TXT[lang]
    eng = ENGINE_NAME[log["engine"]]
    changed = phase in ("set", "seen")
    src, st = ev["source"], ev["state"]
    hl = lambda on, color: f'color:{color};font-weight:bold' if on else 'color:#e6edf3;font-weight:bold'   # noqa: E731
    if log["kind"] == "price":
        row = (f'<table class="mono" style="font-size:24px;border-collapse:collapse;margin-top:18px">'
               f'<tr style="color:#8b949e"><td style="padding:4px 18px 4px 0">product_number</td><td style="padding:4px 18px">standard_cost</td><td style="padding:4px 0 4px 18px">list_price</td></tr>'
               f'<tr><td style="padding:4px 18px 4px 0">{esc(src["product_number"])}</td><td style="padding:4px 18px">{src["standard_cost"]:.2f}</td>'
               f'<td style="padding:4px 0 4px 18px;{hl(changed, "#f0883e")}">{src["list_price"]:.2f}</td></tr></table>')
        right = (f'<div class="mono" style="font-size:24px;line-height:1.8">product_number: "BK-M68B-38"<br>name: "Mountain-200 Black, 38"<br>'
                 f'standard_cost: {st["standard_cost"]:.4f}<br>list_price: <span style="{hl(phase == "seen", "#3fb950")}">{st["list_price"]:.2f}</span></div>')
    else:
        row = (f'<pre class="mono" style="font-size:26px;margin:16px 0">{{\n  _id: {src["_id"]},\n  item: "{src["item"]}",\n'
               f'  location_id: {src["location_id"]},\n  quantity: <span style="{hl(changed, "#f0883e")}">{src["quantity"]}</span>\n}}</pre>')
        right = f'<div style="font-size:22px;color:#8b949e;margin:10px 0 8px">{esc(T["inventory"])}</div>' + "".join(
            f'<div class="mono" style="font-size:26px;padding:6px 0">{esc(i["location"]):<24} quantity '
            f'<span style="{hl(phase == "seen" and i["location"] == "Final Assembly", "#3fb950")}">{i["quantity"]}</span></div>'
            for i in st["inventory"])
    cmd = ev.get("statement") or ""
    pipes = T["pipes_sep"].join(log["pipelines"])
    status = ""
    if phase == "set":
        status = f'<span style="color:#d29922">{esc(T["waiting"])}</span>'
    elif phase == "seen":
        status = f'<span style="color:#3fb950">{esc(T["updated"].format(s=f"{latency:.2f}", n=len(log["pipelines"]), pipes=pipes))}</span>'
    elif phase == "verify":
        status = f'<span style="color:#3fb950">{esc(T["verify_ok"].format(s=verify["seconds"]))}</span>'
    verify_line = (f'<div class="mono" style="font-size:20px;color:#8b949e;margin-top:14px">$ python verify_tapstate.py<br>'
                   f'{esc(verify["line"])}</div>') if verify else ""
    title = T["src_panel"].format(system=T["systems"][log["system"]], engine=eng, table=log["table"])
    border = ENGINE_COLOR[log["engine"]]
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['live_title'])}</h1>
<div style="position:absolute;left:60px;top:120px;width:860px;height:600px;border:2px solid {border};border-radius:12px;background:#161b22;padding:24px;box-sizing:border-box">
 <div style="font-size:24px;color:{border};margin-bottom:16px">{esc(title)}</div>
 <div class="mono" style="font-size:20px;color:#39c5cf;min-height:56px;white-space:pre-wrap">{('$ ' + esc(cmd)) if cmd else ''}</div>{row}</div>
<div style="position:absolute;left:1000px;top:120px;width:860px;height:600px;border:2px solid #39c5cf;border-radius:12px;background:#161b22;padding:24px;box-sizing:border-box">
 <div style="font-size:24px;color:#39c5cf;margin-bottom:16px">{esc(T['state_panel'])}</div>{right}</div>
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
    out = HERE / f"build{VARIANT}_{lang}"
    pages = [(f"flow_{r}", flow_page(lang, r)) for r in range(4)]
    pages += [(f"dsl_{i}", dsl_page(lang, i)) for i in range(len(EMBEDS))]
    log = json.loads((HERE / f"live_change{VARIANT}.json").read_text())
    ev = log["events"]   # start, set, seen, set, seen
    steps = log["steps"]
    pages += [("live_0", live_page(lang, log, ev[0], "start")),
              ("live_1", live_page(lang, log, ev[1], "set")),
              ("live_2", live_page(lang, log, ev[2], "seen", steps[0]["latency_s"])),
              ("live_3", live_page(lang, log, ev[3], "set")),
              ("live_4", live_page(lang, log, ev[4], "seen", steps[1]["latency_s"])),
              ("live_5", live_page(lang, log, ev[4], "verify", verify=log["verify"]))]
    asyncio.run(shoot(pages, out))
    imgs = {n: f"build{VARIANT}_{lang}/{n}.png" for n, _ in pages}

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
