"""Turn the live recordings (record_live.sh) into one silent 1920x1080 MP4 of the demo.

Sections, following demo/README.md: a title card with the customer question; setup A's live run; a
caption card; the Tapstate side (sources, pipelines, one consolidated document); setup C's live run;
the trace report of those two runs, scrolled with two query rows opened; a closing card with both
runs' numbers. Terminal sections replay the recorded bytes at their recorded timing, with idle gaps
capped at 2 s (as asciinema's --idle-time-limit); the agents' output is not edited or sped up.

Rendering happens in headless Chrome (player.html draws on a canvas; MediaRecorder encodes H.264), so
no ffmpeg is needed; remux_mp4.py turns MediaRecorder's fragmented MP4 into a regular one (real duration,
seekable in QuickTime/PowerPoint), keeping the original as *_fragmented.mp4. Also writes one still PNG
per section.

Usage (from demo/): python recording/make_video.py <dab-root> [--sample] [--lang zh]
--lang zh (without --sample) writes recording/sales_agent_demo_zh.mp4 from record_live.sh's zh recordings.
--sample writes recording/sample.mp4 instead: ~3 s of a caption card, ~9 s of setup A's recording,
~4 s of the trace report, to check the look.
--sample --lang zh writes recording/sample_zh.mp4: everything on screen in Simplified Chinese, the
terminal parts from `sales_agent.py --replay --lang zh` of the recorded live runs (no model calls).
Writes recording/sales_agent_demo.mp4, recording/stills/*.png, recording/trace_report.html.
"""
import asyncio
import base64
import json
import re
import subprocess
import sys
from pathlib import Path

from cdp import Chrome

HERE = Path(__file__).resolve().parent
DEMO = HERE.parent
IDLE_CAP = 2.0
COLS, ROWS = 136, 38
PORT = 8765


def term_events(name, cap=IDLE_CAP):
    """[(t, text)] from util-linux script's classic timing log, gaps capped at `cap` seconds."""
    raw = (HERE / f"{name}.out").read_bytes()
    if raw.startswith(b"Script started"):
        raw = raw[raw.index(b"\n") + 1:]
    events, t, off = [], 0.0, 0
    for line in (HERE / f"{name}.timing").read_text().split("\n"):
        if not line.strip():
            continue
        delay, n = line.split()
        t += min(float(delay), cap)
        events.append([round(t, 3), raw[off:off + int(n)].decode("utf-8", "replace")])
        off += int(n)
    # drop script's own trailer if it slipped into the last chunk
    events = [[t, re.sub(r"\n?Script done on .*", "", s, flags=re.S)] for t, s in events]
    return events


def term_seg(name, caption, hold, cap=IDLE_CAP):
    ev = term_events(name, cap)
    return {"kind": "term", "cols": COLS, "rows": ROWS, "events": ev, "dur": round(ev[-1][0] + hold, 2),
            "caption": caption}


FONT_URL = "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf"
FONT_SHA256 = "a3041811a78c361b1de50f953c805e0244951c21c5bd412f7232ef0d899af0da"
FONT = HERE / "fonts" / "NotoSansSC-wght.ttf"


def ensure_font():
    """Noto Sans SC (SIL Open Font License), the whole variable font, for every Chinese frame."""
    import hashlib
    import urllib.request
    if not FONT.exists():
        FONT.parent.mkdir(exist_ok=True)
        urllib.request.urlretrieve(FONT_URL, FONT)
    if hashlib.sha256(FONT.read_bytes()).hexdigest() != FONT_SHA256:
        raise SystemExit(f"{FONT} does not match its sha256; delete it and run again")


LOCAL_FACE = """(() => { const s = document.createElement('style');
  s.textContent = '@font-face{font-family:"Noto Sans SC";src:url("%s") format("truetype");font-weight:100 900}';
  document.head.appendChild(s); return true; })()"""

FONT_LOAD = """(async () => { const t = document.documentElement.textContent;
  for (const w of ['', 'bold ']) await document.fonts.load(w + '15px "Noto Sans SC"', t);
  await document.fonts.ready; return true; })()"""


async def capture_report(report, out, zh=False):
    """Full-page screenshots of the trace report at 1.5x (1920 px wide): closed, then with one query row
    opened in each table. Returns {image: path} and the y positions (image px) the scroll uses."""
    async with Chrome(width=1280, height=720, port=9334) as c:
        await c.send("Emulation.setDeviceMetricsOverride",
                     {"width": 1280, "height": 720, "deviceScaleFactor": 1.5, "mobile": False})
        await c.send("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-color-scheme", "value": "light"}]})
        await c.goto(report.as_uri())
        if zh:
            await c.eval(LOCAL_FACE % FONT.as_uri())
            await c.eval(FONT_LOAD, await_promise=True)
            await asyncio.sleep(1)
        ys = json.loads(await c.eval("""JSON.stringify((() => {
          const y = el => el.getBoundingClientRect().top + scrollY;
          const det = [...document.querySelectorAll('details')];
          const tables = [...document.querySelectorAll('section h3')];
          const pick = (from, re) => det.findIndex((d, i) => i >= from && re.test(d.textContent));
          const firstC = det.findIndex(d => d.closest('section') === tables[1].closest('section'));
          let a = pick(0, /LOWER|lower\\(/); if (a < 0 || a >= firstC) a = pick(0, /work_orders/);
          let cc = pick(firstC, /\\$date/); if (cc < 0) cc = firstC;
          return {cards: y(document.querySelector('.grid2')), hl: y(document.querySelector('.hl')),
                  chart: y(document.querySelector('svg')), tA: y(tables[0]), tC: y(tables[1]),
                  a, c: cc, end: document.documentElement.scrollHeight};
        })())"""))
        s = 1.5
        imgs = {}
        await c.screenshot(out / "report_0.png", full_page=True)
        imgs["r0"] = "report_0.png"
        await c.eval(f"document.querySelectorAll('details')[{ys['a']}].open = true")
        rowA = await c.eval(f"document.querySelectorAll('details')[{ys['a']}].getBoundingClientRect().top + scrollY")
        await c.screenshot(out / "report_1.png", full_page=True)
        imgs["r1"] = "report_1.png"
        tC1 = await c.eval("document.querySelectorAll('section h3')[1].getBoundingClientRect().top + scrollY")
        await c.eval(f"document.querySelectorAll('details')[{ys['c']}].open = true")
        rowC = await c.eval(f"document.querySelectorAll('details')[{ys['c']}].getBoundingClientRect().top + scrollY")
        await c.screenshot(out / "report_2.png", full_page=True)
        imgs["r2"] = "report_2.png"
        end2 = await c.eval("document.documentElement.scrollHeight")
    px = {k: v * s for k, v in ys.items() if k not in ("a", "c")}
    px.update(rowA=rowA * s, tC1=tC1 * s, rowC=rowC * s, end2=end2 * s)
    return imgs, px


def report_seg(px, view_h=1080 - 72, stretch=1.0):
    top = 0
    clamp = lambda y, end: max(0, min(y, end - view_h))   # noqa: E731
    e0, e2 = px["end"], px["end2"]
    k = [  # [time s, image, y]
        [0, "r0", top], [12, "r0", top],                                    # the cards
        [15, "r0", clamp(px["hl"] - 120, e0)], [23, "r0", clamp(px["hl"] - 120, e0)],   # ratio sentence + chart
        [27, "r0", clamp(px["chart"] - 80, e0)], [35, "r0", clamp(px["chart"] - 80, e0)],
        [45, "r0", clamp(px["rowA"] - 300, e0)], [47, "r0", clamp(px["rowA"] - 300, e0)],  # slow scroll to row A
        [47.01, "r1", clamp(px["rowA"] - 300, e0)], [55, "r1", clamp(px["rowA"] - 300, e0)],  # row A opened
        [70, "r1", clamp(px["tC1"] - 60, e0)], [74, "r1", clamp(px["tC1"] - 60, e0)],        # on to Tapstate's calls
        [80, "r1", clamp(px["rowC"] - 300, e0)], [82, "r1", clamp(px["rowC"] - 300, e0)],
        [82.01, "r2", clamp(px["rowC"] - 300, e2)], [90, "r2", clamp(px["rowC"] - 300, e2)],  # row C opened
        [98, "r2", clamp(e2, e2)], [102, "r2", clamp(e2, e2)],
    ]
    k = [[round(t * stretch, 2), im, y] for t, im, y in k]
    return {"kind": "page", "keys": k, "dur": round(102 * stretch, 2),
            "caption": "Trace report: both runs side by side, then every call each agent made (two queries opened)"}


def numbers(run):
    s = json.loads((DEMO / "runs" / run / "summary.json").read_text())
    return s


def record_replay(name, run, lang):
    """A terminal recording of `sales_agent.py --replay` (no model calls), like record_live.sh's."""
    if (HERE / f"{name}.out").exists():
        return
    cmd = (f"stty cols {COLS} rows {ROWS}; export COLUMNS={COLS} LINES={ROWS}; cd {DEMO}; "
           f"{sys.executable} sales_agent.py --replay runs/{run}/stream.jsonl --lang {lang}")
    subprocess.run(["script", "-q", "-e", f"--log-timing={HERE / name}.timing", f"--log-out={HERE / name}.out",
                    "-c", cmd], check=True, stdout=subprocess.DEVNULL)


def sample_zh(dab):
    a_run, c_run = sorted((HERE / "runs.txt").read_text().split())
    sys.path.insert(0, str(DEMO))
    import i18n
    i18n.set_lang("zh")
    question = i18n.question(json.loads((dab / "query_manufacturing" / "query101" / "query.json").read_text()))
    record_replay("a_replay_zh", a_run, "zh")
    record_replay("c_replay_zh", c_run, "zh")
    report = HERE / "trace_report_zh.html"
    subprocess.run([sys.executable, str(DEMO / "trace_report.py"), str(DEMO / "runs" / a_run),
                    str(DEMO / "runs" / c_run), "-o", str(report), "--lang", "zh"], check=True)
    imgs, px = asyncio.run(capture_report(report, HERE, zh=True))

    def cut(name, secs, caption):
        seg = term_seg(name, caption, hold=0)
        seg["events"] = [e for e in seg["events"] if e[0] <= secs]
        seg["dur"] = secs + 0.3
        return seg
    segs = [
        {"kind": "card", "dur": 4, "top": 190, "caption": "客户的问题，发给销售助理智能体",
         "blocks": [{"text": "销售助理智能体演示", "size": 30, "color": "#8b949e", "gap": 36},
                    {"text": question, "size": 34, "width": 1500, "align": "left", "gap": 60},
                    {"text": "演示一：六个在线系统", "size": 56, "bold": True, "color": "#f0883e"},
                    {"text": "ERP、PLM 和成本核算在 Postgres · MES 和采购在 MySQL · WMS 在 MongoDB",
                     "size": 28, "color": "#8b949e"}]},
        cut("a_replay_zh", 12, "录制的现场运行（回放）：智能体拿到六个连接串，在 Postgres、MySQL 和 MongoDB 之间查询"),
        cut("c_replay_zh", 6, "录制的现场运行（回放）：同一个智能体、同一个问题，只连接 Tapstate 的整合状态"),
        {"kind": "page", "dur": 8, "caption": "运行对比报告：两次运行并排对比，以及 token 消耗曲线",
         "keys": [[0, "r0", 0], [3, "r0", 0], [5, "r0", min(px["hl"] - 120, px["end"] - 1008)],
                  [8, "r0", min(px["hl"] - 120, px["end"] - 1008)]]},
    ]
    (HERE / "timeline.json").write_text(json.dumps({"lang": "zh", "segments": segs, "images": imgs}))
    total = sum(x["dur"] for x in segs)
    print(f"timeline: {total:.1f} s, sections: " + ", ".join(f"{x['kind']} {x['dur']:.1f}s" for x in segs))
    asyncio.run(render(segs, total, sample=True, names=["sample_zh_1_title", "sample_zh_2_setup_a",
                                                        "sample_zh_3_setup_c", "sample_zh_4_report"],
                       out_name="sample_zh.mp4"))


TEXT = {
    "en": {
        "title_kicker": "Sales agent demo", "title_caption": "The customer's question, sent to the sales agent",
        "demo1": "Demo 1: six live systems",
        "engines": "ERP, PLM and costing on Postgres · MES and procurement on MySQL · WMS on MongoDB",
        "a_caption": "Live: the agent gets six connection strings and works across Postgres, MySQL and MongoDB",
        "card2_caption": "Next: the same question against Tapstate",
        "demo2": "Demo 2: the same six databases, consolidated by Tapstate",
        "demo2_sub": "Tapstate captures every change from the six systems and keeps one consolidated MongoDB "
                     "database, mfg_state.",
        "tap_caption": "Live: Tapstate's six source definitions, its running pipelines, and one consolidated "
                       "product document",
        "c_caption": "Live: the same agent and question, with one connection to Tapstate's consolidated state",
        "report_caption": "Trace report: both runs side by side, then every call each agent made (two queries opened)",
        "close_caption": "The two live runs in this video", "close_head": "Same agent, same question, same data",
        "close_ratio": "Against six live systems the agent used {r:.1f}× the tokens it used against Tapstate.",
        "rows": ["", "Six live systems", "Tapstate"], "correct": "Correct answer", "yes": "yes", "no": "NO",
        "tokens": "Tokens", "rt": "Round trips to the model", "queries": "Queries issued",
        "q_a": "{n} (6 systems)", "q_c": "{n} (1 database)", "failed": "Failed calls", "cost": "Cost (API-equivalent)",
    },
    "zh": {
        "title_kicker": "销售助理智能体演示", "title_caption": "客户的问题，发给销售助理智能体",
        "demo1": "演示一：六个在线系统",
        "engines": "ERP、PLM 和成本核算在 Postgres · MES 和采购在 MySQL · WMS 在 MongoDB",
        "a_caption": "现场运行：智能体拿到六个连接串，在 Postgres、MySQL 和 MongoDB 之间查询",
        "card2_caption": "接下来：同一个问题，改为查询 Tapstate",
        "demo2": "演示二：同样的六个数据库，由 Tapstate 整合",
        "demo2_sub": "Tapstate 捕获六个系统的每一次变更，并维护一个整合后的 MongoDB 数据库：mfg_state。",
        "tap_caption": "现场演示：Tapstate 的六个数据源定义、正在运行的管道，以及一个整合后的产品文档",
        "c_caption": "现场运行：同一个智能体、同一个问题，只连接 Tapstate 的整合状态",
        "report_caption": "运行对比报告：两次运行并排对比，然后是每一次调用（展开其中两条查询）",
        "close_caption": "本视频中的两次现场运行", "close_head": "同样的智能体、同样的问题、同样的数据",
        "close_ratio": "面对六个在线系统，智能体消耗的 token 是面对 Tapstate 时的 {r:.1f} 倍。",
        "rows": ["", "六个在线系统", "Tapstate"], "correct": "回答正确", "yes": "是", "no": "否",
        "tokens": "token 总数", "rt": "与模型的往返次数", "queries": "发出的查询",
        "q_a": "{n}（6 个系统）", "q_c": "{n}（1 个数据库）", "failed": "失败的调用", "cost": "成本（按 API 价格折算）",
    },
}


BUILD_CAPTIONS = {
    "zh": ["Tapstate 内部：六个源系统 → 统一产品编号的暂存管道 → 嵌套出状态（由真实工作区自动生成）",
           "声明式管道：每一行 embed 产出文档的一部分，并标明它来自哪个系统",
           {"price": "实测：修改成本核算（Postgres）中的当前标价，Tapstate 的实时文档随之更新，随后改回并校验",
            "stock": "实测：修改 WMS（MongoDB）中的库存，Tapstate 的实时文档随之更新，随后改回并校验"}],
    "en": ["Inside Tapstate: six sources → staging pipelines that unify the product key → nested state (drawn from the workspace)",
           "A declarative pipeline: each embed line produces one part of the document, from one system",
           {"price": "Measured: a list-price change in costing (Postgres) reaches Tapstate's live document, then set back and verified",
            "stock": "Measured: a WMS (MongoDB) stock change reaches Tapstate's live document, then set back and verified"}],
}


def build_segments(lang):
    """The "how the state is built" section (build_section.py), from live_change.py's measured log."""
    from build_section import section
    ensure_font()
    kind = json.loads((HERE / "live_change.json").read_text()).get("kind", "stock")
    caps = BUILD_CAPTIONS[lang]
    return section(lang, [caps[0], caps[1], caps[2][kind]])


def main():
    dab = Path(sys.argv[1]).resolve()
    lang = sys.argv[sys.argv.index("--lang") + 1] if "--lang" in sys.argv else "en"
    sample = "--sample" in sys.argv
    if "--sample-build" in sys.argv:
        segs, imgs, log = build_segments(lang)
        (HERE / "timeline.json").write_text(json.dumps({"lang": lang, "segments": segs, "images": imgs}))
        total = sum(x["dur"] for x in segs)
        print(f"timeline: {total:.1f} s; latencies " + ", ".join(f"{st['from']}->{st['to']} {st['latency_s']} s"
                                                            for st in log["steps"]))
        name = sys.argv[sys.argv.index("--name") + 1] if "--name" in sys.argv else f"sample_build_{lang}"
        return asyncio.run(render(segs, total, sample=True, names=[f"{name}_{i}" for i in (1, 2, 3)],
                                  out_name=f"{name}.mp4"))
    if lang == "zh" and sample:
        return sample_zh(dab)
    if lang == "zh":
        ensure_font()
    sfx = "_zh" if lang == "zh" else ""
    T = TEXT[lang]
    a_run, c_run = sorted((HERE / f"runs{sfx}.txt").read_text().split())   # A_..., C_...
    A, C = numbers(a_run), numbers(c_run)
    question = json.loads((dab / "query_manufacturing" / "query101" / "query.json").read_text())
    if lang == "zh":
        sys.path.insert(0, str(DEMO))
        import i18n
        i18n.set_lang("zh")
        question = i18n.question(question)
    report = HERE / f"trace_report{sfx}.html"
    subprocess.run([sys.executable, str(DEMO / "trace_report.py"), str(DEMO / "runs" / a_run),
                    str(DEMO / "runs" / c_run), "-o", str(report), "--lang", lang], check=True)
    imgs, px = asyncio.run(capture_report(report, HERE, zh=lang == "zh"))

    ratio = A["tokens"]["total"] / C["tokens"]["total"]
    yes = lambda s: T["yes"] if s["correct"] else T["no"]   # noqa: E731
    rep = report_seg(px, stretch=1.0 if lang == "en" else 1.28)
    rep["caption"] = T["report_caption"]
    segs = [
        {"kind": "card", "dur": 15 if lang == "en" else 18, "top": 200 if lang == "en" else 190,
         "caption": T["title_caption"],
         "blocks": [{"text": T["title_kicker"], "size": 30, "color": "#8b949e", "gap": 40},
                    {"text": question, "size": 36 if lang == "en" else 34, "width": 1500, "align": "left", "gap": 70},
                    {"text": T["demo1"], "size": 56, "bold": True, "color": "#f0883e"},
                    {"text": T["engines"], "size": 28, "color": "#8b949e"}]},
        term_seg(f"a_run{sfx}", T["a_caption"], hold=8 if lang == "en" else 10),
        {"kind": "card", "dur": 8, "top": 430, "caption": T["card2_caption"],
         "blocks": [{"text": T["demo2"], "size": 52, "bold": True, "color": "#39c5cf", "width": 1700},
                    {"text": T["demo2_sub"], "size": 30, "color": "#8b949e", "width": 1500}]},
        # the Tapstate segment's pauses are scripted reading time (tapstate_side.sh), not idle waiting
        term_seg(f"tapstate{sfx}", T["tap_caption"], hold=4, cap=IDLE_CAP if lang == "en" else 8.0),
        term_seg(f"c_run{sfx}", T["c_caption"], hold=8 if lang == "en" else 10),
        rep,
        {"kind": "card", "dur": 25, "top": 250, "caption": T["close_caption"],
         "blocks": [{"text": T["close_head"], "size": 46, "bold": True, "gap": 30},
                    {"text": T["close_ratio"].format(r=ratio), "size": 32, "color": "#8b949e", "width": 1500,
                     "gap": 50}],
         "table": {"rows": [
             T["rows"],
             [T["correct"], yes(A), yes(C)],
             [T["tokens"], f"{A['tokens']['total']:,}", f"{C['tokens']['total']:,}"],
             [T["rt"], str(A["round_trips"]), str(C["round_trips"])],
             [T["queries"], T["q_a"].format(n=A["queries"]), T["q_c"].format(n=C["queries"])],
             [T["failed"], str(A["errors"]), str(C["errors"])],
             [T["cost"], f"${A['cost_usd']:.2f}", f"${C['cost_usd']:.2f}"],
         ]}},
    ]
    if sample:
        a = term_seg("a_run", segs[1]["caption"], hold=0)
        a["events"] = [e for e in a["events"] if e[0] <= 9.0]
        a["dur"] = 9.5
        segs = [dict(segs[2], dur=3), a,
                {"kind": "page", "dur": 4, "caption": segs[5]["caption"],
                 "keys": [[0, "r0", 0], [1.5, "r0", 0], [4, "r0", min(px["hl"] - 120, px["end"] - 1008)]]}]
    timeline = {"lang": lang, "segments": segs, "images": {k: v for k, v in imgs.items()}}
    (HERE / "timeline.json").write_text(json.dumps(timeline))
    total = sum(s["dur"] for s in segs)
    print(f"timeline: {total:.0f} s ({total / 60:.1f} min), sections: " +
          ", ".join(f"{s['kind']} {s['dur']:.0f}s" for s in segs))
    names = None if sample else [f"{n}{sfx}" for n in ("1_title", "2_setup_a", "3_caption", "4_tapstate",
                                                        "5_setup_c", "6_report", "7_closing")]
    out_name = None if sample else f"sales_agent_demo{sfx}.mp4"
    asyncio.run(render(segs, total, sample=sample, names=names, out_name=out_name))


async def render(segs, total, sample=False, names=None, out_name=None):
    server = subprocess.Popen([sys.executable, "-m", "http.server", str(PORT), "--bind", "127.0.0.1"],
                              cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        await asyncio.sleep(1)
        async with Chrome(port=9335) as c:
            await c.goto(f"http://127.0.0.1:{PORT}/player.html")
            info = await c.eval("load('timeline.json')", await_promise=True)
            print(f"player loaded: {info}")
            stills = HERE / "stills"
            stills.mkdir(exist_ok=True)
            names = names or (["sample_1_caption", "sample_2_terminal", "sample_3_report"] if sample else
                              ["1_title", "2_setup_a", "3_caption", "4_tapstate", "5_setup_c", "6_report", "7_closing"])
            t = 0.0
            for s, name in zip(segs, names):
                at = t + (s["dur"] - (8 if s["kind"] == "term" else 0)) * (0.98 if s["kind"] == "term" else 0.5)
                if s["kind"] == "page":
                    at = t + min(50, s["dur"] - 0.5)
                    if s.get("still_at") is not None:
                        at = t + s["still_at"]
                if sample and s["kind"] == "term":
                    at = t + s["dur"] - 0.2
                png = await c.eval(f"still({at})")
                (stills / f"{name}.png").write_bytes(base64.b64decode(png.split(",", 1)[1]))
                t += s["dur"]
            print(f"stills: {stills}")
            size = await c.eval("record(3500000)", await_promise=True)
            out = HERE / (out_name or ("sample.mp4" if sample else "sales_agent_demo.mp4"))
            frag = out.with_name(out.stem + "_fragmented.mp4")   # MediaRecorder's file, before remuxing
            with frag.open("wb") as f:
                step = 4 * 1024 * 1024
                for off in range(0, size, step):
                    f.write(base64.b64decode(await c.eval(f"chunk({off}, {step})")))
            from remux_mp4 import remux
            frames, secs, _ = remux(frag, out)   # a regular MP4: real duration, seekable everywhere
            print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {secs:.1f} s, {frames} frames)")
    finally:
        server.terminate()


if __name__ == "__main__":
    main()
