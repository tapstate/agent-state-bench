"""Scoring a freshness answer (FRESHNESS.md §5).

An answer is checked with answer_check.holds after dates and times are removed: every question
states the current time, and answers repeat it, so "2026-07-01" would otherwise supply a stray 1.

  correct  the answer at the checkpoint holds
  stale    it doesn't, and the answer a warehouse loaded at the last midnight would give holds
  other    neither
"""
import re

from answer_check import holds

DATETIME = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b|\b\d{1,2}:\d{2}\b")
# an answer that says it reflects the last load rather than now (reported, not scored)
DISCLOSES = re.compile(r"as of (?:the )?(?:last|latest|most recent|previous|nightly)|last (?:\w+ )?load|"
                       r"loaded at|nightly|since (?:the )?(?:last )?(?:load|midnight)|may not (?:yet )?(?:reflect|include)|"
                       r"not (?:yet )?(?:reflect|include)|stale|out of date|up to midnight|(?:was|were) (?:last )?loaded|loaded on|loaded (?:today|yesterday|last night)|warehouse load|"
                       r"refreshed once a (?:night|day)|not (?:the )?(?:\w+ ){0,2}(?:as of )?right now", re.I)


def strip_times(text):
    return DATETIME.sub(" ", text)


def verdict(q, text):
    t = strip_times(text or "")
    if holds(q["answer"], t):
        return "correct"
    if holds(q["warehouse_answer"], t):
        return "stale"
    return "other"


def discloses(text):
    return bool(DISCLOSES.search(text or ""))
