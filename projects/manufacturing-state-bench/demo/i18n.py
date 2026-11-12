"""Display strings for the sales-agent demo, in English (default) and Simplified Chinese (--lang zh).

Display only: the agent's prompt, its queries, the saved run files and the checks stay in English.
Product names, store names, queries, connection strings, numbers and units are never translated.
"""
import re
import unicodedata

LANG = "en"

ZH = {
    # sales_agent.py
    "Sales agent, setup": "销售助理智能体，方案",
    "six live source systems": "六个在线源系统",
    "Tapstate consolidated state": "Tapstate 整合状态",
    "recorded run": "录制的运行",
    "Customer question:": "客户问题：",
    "Connections given to the agent": "提供给智能体的连接",
    "rt": "往返",
    "ok": "成功",
    "error": "出错",
    "chars back": "字符返回",
    "tokens so far": "累计 token",
    "--- Answer ---": "--- 回答 ---",
    "(no answer)": "（没有回答）",
    "correct": "正确",
    "not correct": "不正确",
    "--- The work ---": "--- 工作量 ---",
    "round trips to the model": "与模型的往返次数",
    "queries issued": "发出的查询",
    "failed calls": "失败的调用",
    "tokens": "token 总数",
    "input": "输入",
    "cache write": "缓存写入",
    "cache read": "缓存读取",
    "output": "输出",
    "cost (API-equivalent)": "成本（按 API 价格折算）",
    "wall time": "耗时",
    "saved": "已保存",
    "run record": "运行记录",
    "translation": "译文",
    # system names
    "ERP sales": "ERP 销售",
    "Procurement": "采购",
    "Cost accounting": "成本核算",
    "Tapstate state": "Tapstate 状态",
    # trace_report.py
    "Sales agent: what it took to answer one customer": "销售助理智能体：回答一位客户需要付出多少",
    "Six live systems": "六个在线系统",
    "Tapstate": "Tapstate",
    "tokens_unit": "token",
    "Round trips to the model": "与模型的往返次数",
    "Queries issued": "发出的查询",
    "By system": "按系统",
    "Failed calls": "失败的调用",
    "Cost (API-equivalent)": "成本（按 API 价格折算）",
    "Wall time": "耗时",
    "Answer": "回答",
    "tool calls, in order": "工具调用（按顺序）",
    "Tokens spent over the run": "整个运行过程中消耗的 token",
    "every call": "每一次调用",
    "#": "#",
    "Round trip": "往返",
    "Tool": "工具",
    "System": "系统",
    "Query or code": "查询或代码",
    "Chars back": "返回字符数",
    "Tokens so far": "累计 token",
    "ratio": ("面对六个在线系统，智能体消耗的 token 是面对 Tapstate 时的 <b>{ratio:.1f} 倍</b>，"
              "与模型往返 {ra} 次，而不是 {rc} 次。"),
    "footer": ("模型：{model}。token 数为输入、缓存写入、缓存读取与输出之和，与基准测试的口径一致。"
               "运行记录：{a}，{c}。"),
}

QUESTION_ZH = {
    "A customer writes in: \"Hi, this is Rural Cycle Emporium.":
        "一位客户来信：“您好，这里是 Rural Cycle Emporium。我们想补订自 2024 年 7 月 1 日以来的订单中，"
        "从贵司购买数量最多的那款自行车。是哪一款？当前标价是多少？目前所有库位合计能立即发货多少台？"
        "贵厂在 2025 年第二季度（2025 年 4 月至 6 月完工的生产工单）完工了多少台？如果需要再生产，"
        "在为它外购的零部件中，最长的供应商交货期是多少天？” 请按以下格式回答：“车型：名称，当前标价：$X，"
        "现有库存：N，2025年第二季度完工：N，最长采购交期：N 天”。",
}

ANSWER_LABELS_ZH = [("bike", "车型"), ("list price", "当前标价"), ("in stock", "现有库存"),
                    ("built in Q2 2025", "2025年第二季度完工"), ("longest lead time", "最长采购交期")]


def set_lang(lang):
    global LANG
    LANG = lang or "en"


def t(s):
    """The display string for s in the current language (s itself in English or when untranslated)."""
    if LANG == "zh":
        return ZH.get(s, s)
    return {"tokens_unit": "tokens", "run record": "recorded run:"}.get(s, s)


def question(q):
    if LANG == "zh" and q:
        for start, zh in QUESTION_ZH.items():
            if q.startswith(start):
                return zh
    return q


def answer_translation(answer):
    """The answer with its labels in Chinese, or None (English, or not in the expected form)."""
    if LANG != "zh" or not answer:
        return None
    labels = dict(ANSWER_LABELS_ZH)
    parts = re.split(r"\b(" + "|".join(re.escape(en) for en in labels) + r"):\s*", answer)
    if len(parts) < 3:
        return None
    pairs = zip(parts[1::2], parts[2::2])   # values keep their own commas (a product name can hold one)
    return "；".join(f"{labels[en]}：{re.sub(r'(\d+) days?\b', r'\1 天', v.strip().rstrip(',').strip())}"
                    for en, v in pairs)


def width(s):
    """Terminal columns s takes (CJK characters take two)."""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def pad(s, n):
    """s left-aligned in n terminal columns."""
    s = str(s)
    return s + " " * max(0, n - width(s))
