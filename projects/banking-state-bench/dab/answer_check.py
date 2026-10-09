"""Answer checking for the bank questions. make_queries.py embeds this file in every validate.py.

Specs (see questions.py):
  ["count", n]                  an integer equal to n appears as a standalone number
  ["money", x, tol]             a number within tol of x appears (commas, Indian grouping, a
                                rupee sign or "Rs" are fine)
  ["text", s]                   s appears, ignoring case and runs of whitespace
  ["ids", must]                 every id in must appears as a whole token
  ["labeled", [[label, spec]]]  for each pair, at some occurrence of the label (a string, or a list of
                                alternatives) the text after it satisfies spec; that text runs to the
                                end of the line, the next label of this spec, or WINDOW characters,
                                whichever comes first
  ["all", [spec, ...]]          every spec holds

Version 2 (2026-10-09, after the Opus pilot): an id list no longer fails an answer for mentioning
a non-qualifying id (agents name them to say they are excluded), and a label's value may follow
other numbers on the same line (table rows such as "| HNI | 80 | 279,643,333.35 |").
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
                    if holds(sub, window):
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
