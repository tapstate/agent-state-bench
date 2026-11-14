"""The demo's "three ways, one pipeline" beat: how a Tapstate pipeline gets made.

1. AI-assisted with a human check: ai_draft.py's real Claude Code run (fast-forwarded: idle gaps capped
   short, and said so), ending on `tapstate validate` of the draft.
2. The visual builder: ui_capture.py's screen recording of Tapstate's own web UI, from the overview to
   mfg_products in the graph editor.
3. The DSL: dsl_side.sh's terminal, `tapstate validate` and `desc` of product_state.example.tap.yml.
Then one frame with the three side by side, converging on the same pipeline and the same live state.

Used by make_video.py --sample-three-ways.
"""
import asyncio
import json
from pathlib import Path

from build_section import CSS, esc, shoot

HERE = Path(__file__).resolve().parent

TXT = {
    "zh": {
        "ai_caption": "① AI 辅助 + 人工确认：Claude Code 只读取导出的数据源定义、Tapstate 发现的表结构和语法说明（未连接 Tapstate）起草管道，再用 CLI 校验（快进）",
        "builder_caption": "② 可视化构建器：Tapstate 自带的 Web 界面，mfg_products 管道的节点图（实际录屏，略去页面加载；编辑器在 0.7.0 中尚未汉化）",
        "dsl_caption": "③ DSL（自动化）：版本控制中的 .tap.yml，由脚本或 CI 用 CLI 校验后发布",
        "conv_caption": "三种方式，同一种产物：经人工确认的 .tap.yml 管道 → 同样的实时状态",
        "title": "三种方式，同一条管道",
        "c1": "① AI 辅助 + 人工确认", "c2": "② 可视化构建器", "c3": "③ DSL（自动化）",
        "c1_body": "Claude Code 根据一句话起草 .tap.yml<br>读取：数据源定义 · 表结构 · 语法说明<br>tapstate validate：<b style='color:#ff7b72'>发现 1 处错误</b>，须经人工修正后才能发布",
        "c1_note": "Tapstate 自带的“使用 AI 构建”将在下一版本提供",
        "c2_body": "Tapstate Web 界面中的节点图<br>数据源 → 转换 → 嵌套 → 视图",
        "c3_body": "product_state.example.tap.yml<br>tapstate validate <b style='color:#3fb950'>✓</b> · tapstate desc <b style='color:#3fb950'>✓</b><br>脚本 / CI 自动发布",
        "target": "同一条管道（.tap.yml）→ 同样的实时状态：views.mfg_product",
    },
    "en": {
        "ai_caption": "① AI-assisted + human check: Claude Code drafts from exported source definitions, Tapstate's discovered schemas and the grammar (no connection to Tapstate), then the CLI checks it (fast-forwarded)",
        "builder_caption": "② Visual builder: Tapstate's own web UI, the mfg_products pipeline as a graph (screen recording, page loads cut)",
        "dsl_caption": "③ DSL (automation): a .tap.yml under version control, checked and applied by a script or CI",
        "conv_caption": "Three ways, one artifact: a human-checked .tap.yml pipeline → the same live state",
        "title": "Three ways, one pipeline",
        "c1": "① AI-assisted + human check", "c2": "② Visual builder", "c3": "③ DSL (automation)",
        "c1_body": "Claude Code drafts the .tap.yml from one sentence<br>reads: sources · schemas · grammar<br>tapstate validate: <b style='color:#ff7b72'>1 error found</b>, to be fixed by a person before it is applied",
        "c1_note": "Tapstate's built-in Build with AI arrives in the next release",
        "c2_body": "the graph in Tapstate's web UI<br>sources → transforms → nest → view",
        "c3_body": "product_state.example.tap.yml<br>tapstate validate <b style='color:#3fb950'>✓</b> · tapstate desc <b style='color:#3fb950'>✓</b><br>applied by a script / CI",
        "target": "the same pipeline (.tap.yml) → the same live state: views.mfg_product",
    },
}


def convergence_page(lang, builder_img):
    T = TXT[lang]
    col = lambda title, body, extra="", img="": (   # noqa: E731
        f'<div style="width:540px;border:2px solid #30363d;border-radius:14px;background:#161b22;padding:22px 24px;box-sizing:border-box">'
        f'<div style="font:700 30px Noto Sans SC,DejaVu Sans;color:#e6edf3;margin-bottom:14px">{esc(title)}</div>'
        + (f'<img src="{img}" style="width:490px;border-radius:8px;border:1px solid #30363d;margin-bottom:12px">' if img else "")
        + f'<div style="font-size:22px;line-height:1.6;color:#c9d1d9">{body}</div>'
        + (f'<div style="font-size:18px;color:#8b949e;margin-top:12px">{esc(extra)}</div>' if extra else "") + '</div>')
    y1, y2, y3 = 390, 590, 350     # just under each card (the middle one holds the screenshot)
    return f"""<!doctype html><meta charset="utf-8">{CSS}<body><h1>{esc(T['title'])}</h1>
<div style="position:absolute;left:60px;top:120px;width:1800px;display:flex;justify-content:space-between;align-items:flex-start">
{col(T['c1'], T['c1_body'], T['c1_note'])}{col(T['c2'], T['c2_body'], img=builder_img)}{col(T['c3'], T['c3_body'])}</div>
<svg width="1920" height="1008" style="position:absolute;left:0;top:0;pointer-events:none">
<defs><marker id="ah" markerWidth="12" markerHeight="10" refX="11" refY="5" orient="auto"><path d="M0,0 L12,5 L0,10 z" fill="#39c5cf"/></marker></defs>
<path d="M330,{y1} C330,720 960,700 960,800" stroke="#39c5cf" stroke-width="3" fill="none" marker-end="url(#ah)"/>
<path d="M960,{y2} L960,800" stroke="#39c5cf" stroke-width="3" fill="none" marker-end="url(#ah)"/>
<path d="M1590,{y3} C1590,720 960,700 960,800" stroke="#39c5cf" stroke-width="3" fill="none" marker-end="url(#ah)"/></svg>
<div style="position:absolute;left:360px;top:815px;width:1200px;text-align:center;border:2px solid #39c5cf;border-radius:14px;
 background:#0f2a2e;padding:22px;font:700 32px Noto Sans SC,DejaVu Sans;color:#39c5cf">{esc(T['target'])}</div></body>"""


def segments(lang, term_seg):
    """The beat as player segments, and the images it needs."""
    T = TXT[lang]
    sfx = "_zh" if lang == "zh" else ""
    ai = term_seg(f"ai{sfx}", T["ai_caption"], hold=3.5, cap=0.12)       # fast-forwarded, said in the caption
    frames = json.loads((HERE / f"ui_{lang}" / "frames.json").read_text())
    start = next(t for t, _ in frames if t >= 8.6)          # from the overview (in the chosen language) onwards
    # real frames in order; a still screen while a page loads is cut to at most 0.6 s (said in the caption)
    keys, clock, prev = [], 0.0, None
    for i, (t, name) in enumerate(frames):
        if t < start:
            continue
        clock += 0 if prev is None else min(t - prev, 0.6)
        prev = t
        keys.append([round(clock, 3), f"ui_{i}", 0])
    imgs = {f"ui_{i}": f"ui_{lang}/{name}" for i, (t, name) in enumerate(frames) if t >= start}
    builder = {"kind": "page", "keys": keys, "dur": round(keys[-1][0] + 1.5, 2), "caption": T["builder_caption"],
               "still_at": keys[-1][0] + 1}
    dsl = term_seg(f"dsl{sfx}", T["dsl_caption"], hold=2.5, cap=1.0)
    last_ui = frames[-1][1]
    asyncio.run(shoot([("three_ways", convergence_page(lang, f"../ui_{lang}/{last_ui}"))], HERE / f"three_ways_{lang}"))
    imgs["conv"] = f"three_ways_{lang}/three_ways.png"
    conv = {"kind": "page", "keys": [[0, "conv", 0]], "dur": 5, "caption": T["conv_caption"], "still_at": 3}
    return [ai, builder, dsl, conv], imgs
