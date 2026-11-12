"""Answer checking for the manufacturing questions. make_queries.py embeds this file in every validate.py.

Specs (see questions.py):
  ["count", n]                  an integer equal to n appears as a standalone number
  ["money", x, tol]             a number within tol of x appears (commas or a dollar
                                sign are fine)
  ["text", s]                   s appears, ignoring case and runs of whitespace
  ["ids", must]                 every id in must appears as a whole token
  ["labeled", [[label, spec]]]  for each pair, at some occurrence of the label (a string, or a list of
                                alternatives) the text after it satisfies spec; that text runs to the
                                end of the line, the next label of this spec, or WINDOW characters,
                                whichever comes first. A count or amount may instead be the last
                                number before the label (version 3, see below)
  ["all", [spec, ...]]          every spec holds

Based on the bank benchmark's answer_check.py (version 2, after its validator correction).
Version 3 (2026-11-12, after the first scoring of the Opus batch): a labeled count or amount may also
be the last number before its label on the same line ("scrapped 58 Bikes units and 886 Components
units"), not only a number after it. Applied identically to both setups; SOURCE_SPLIT.md §9 lists
every verdict it changed.
"""
import re

NUMBER = re.compile(r"(?<![\w])\d[\d,]*(?:\.\d+)?(?![\w])")   # not inside an id such as CARD60216
WINDOW = 80


def numbers(text):
    out = []
    for m in NUMBER.finditer(text):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            pass
    return out


def squash(s):
    return re.sub(r"\s+", " ", s).strip().lower()


def value_before(sub, text, start, others):
    """Version 3: a count or amount may also come just before its label, as in "58 units in the Bikes
    category". Only the last number before the label counts, and only back to the start of the line
    or the end of another label of the same spec."""
    if sub[0] not in ("count", "money"):
        return False
    line_start = text.rfind("\n", 0, start) + 1
    before = text[max(line_start, start - WINDOW):start]
    ends = [m.end() for o in others for m in re.finditer(re.escape(o), before, re.I)]
    if ends:
        before = before[max(ends):]
    nums = numbers(before)
    return bool(nums) and holds(sub, NUMBER_TEXT.format(nums[-1]))


NUMBER_TEXT = "{:.6f}"


def holds(spec, text):
    kind = spec[0]
    if kind in ("count", "money"):
        nums = numbers(text)
        if kind == "count":
            return any(v == spec[1] for v in nums)
        return any(abs(v - spec[1]) <= spec[2] for v in nums)
    if kind == "text":
        return squash(spec[1]) in squash(text)
    if kind == "ids":
        return all(re.search(rf"(?<![\w]){re.escape(i)}(?![\w])", text, re.I) for i in spec[1])
    if kind == "labeled":
        alts = [lb if isinstance(lb, list) else [lb] for lb, _ in spec[1]]
        every = [x for a in alts for x in a]
        for labels, (_, sub) in zip(alts, spec[1]):
            others = [x for x in every if x not in labels]
            ok = False
            for lb in labels:
                for m in re.finditer(re.escape(lb), text, re.I):
                    window = text[m.end():m.end() + WINDOW].split("\n", 1)[0]
                    cut = [i for i in (re.search(re.escape(o), window, re.I) for o in others) if i]
                    if cut:
                        window = window[:min(i.start() for i in cut)]
                    if holds(sub, window) or value_before(sub, text, m.start(), others):
                        ok = True
                        break
                if ok:
                    break
            if not ok:
                return False
        return True
    if kind == "all":
        return all(holds(s, text) for s in spec[1])
    raise ValueError(f"unknown spec {kind}")


def describe(spec):
    kind = spec[0]
    if kind == "count":
        return str(spec[1])
    if kind == "money":
        return f"{spec[1]:.2f}"
    if kind == "text":
        return spec[1]
    if kind == "ids":
        return ", ".join(spec[1])
    if kind == "labeled":
        return "; ".join(f"{(l[0] if isinstance(l, list) else l)}: {describe(s)}" for l, s in spec[1])
    return "; ".join(describe(s) for s in spec[1])
