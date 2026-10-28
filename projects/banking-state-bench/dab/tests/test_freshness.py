"""Self-checks of the freshness extension's timeline, questions and scoring (FRESHNESS.md). No
databases or model calls; needs dab/data/db.json. Run from dab/: python -m unittest discover -s tests -v
"""
import json
import sys
import unittest
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "freshness"))

import common  # noqa: E402

HAVE_DATA = common.DB_JSON.exists()
if HAVE_DATA:
    import fresh_check  # noqa: E402
    import fresh_questions  # noqa: E402
    import timeline as tl  # noqa: E402

    CUSTOMERS = common.load_customers()
    EVENTS = tl.events(CUSTOMERS)


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class TimelineTests(unittest.TestCase):
    def test_replaying_every_event_ends_at_the_published_dataset(self):
        self.assertEqual(tl.state_at(CUSTOMERS, {e["ref"] for e in EVENTS}), CUSTOMERS)

    def test_the_ledger_holds_at_every_step_of_the_replay(self):
        start = tl.state_at(CUSTOMERS, set())
        bal = {a: x["available_balance"] for c in start.values() for a, x in c["accounts"].items()}
        for e in EVENTS:
            t = e["txn"]
            self.assertAlmostEqual(bal[e["account_id"]] + t["amount"], t["balance_after"], places=2, msg=e["ref"])
            bal[e["account_id"]] = t["balance_after"]

    def test_events_fall_in_business_hours_on_their_own_day(self):
        for e in EVENTS:
            d = tl.day_of(e["t"])
            self.assertEqual(tl.day_date(d).isoformat(), e["txn"]["date"])
            self.assertTrue(d * tl.BLOCK_S + tl.NIGHT_S <= e["t"] <= (d + 1) * tl.BLOCK_S, e["ref"])

    def test_an_episode_never_spans_a_nightly_load(self):
        for d, k, t in tl.checkpoints():
            self.assertGreaterEqual(t - tl.MARGIN_S, d * tl.BLOCK_S + tl.NIGHT_S)
            self.assertLessEqual(t + tl.CAP_S, (d + 1) * tl.BLOCK_S)
        ts = [t for _, _, t in tl.checkpoints()]
        self.assertTrue(all(b - a >= tl.CAP_S + tl.MARGIN_S for a, b in zip(ts, ts[1:])))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class FreshQuestionTests(unittest.TestCase):
    qset, frozen, kinds, ids = "core", "freshness_questions.json", {"changed": 16, "control": 6, "portfolio": 2}, 1

    @classmethod
    def setUpClass(cls):
        cls.qs = json.loads(json.dumps(fresh_questions.build(cls.qset), ensure_ascii=False))

    def test_frozen_questions_match_the_data(self):
        self.assertEqual(self.qs, json.loads((HERE / "freshness" / self.frozen).read_text()))

    def test_composition(self):
        n = defaultdict(int)
        for q in self.qs:
            n[q["kind"]] += 1
        self.assertEqual(dict(n), self.kinds)
        self.assertEqual([q["id"] for q in self.qs], list(range(self.ids, self.ids + 24)))
        cids = [q["customer_id"] for q in self.qs if q["customer_id"]]
        self.assertEqual(len(cids), len(set(cids)))

    def test_accounts_are_quiet_and_changed_means_changed(self):
        for q in self.qs:
            if q["kind"] == "portfolio":
                self.assertTrue(fresh_questions.differs(q["answer"], q["warehouse_answer"]))
                continue
            accs, t, d = set(q["accounts"]), q["t"], q["day"]
            self.assertEqual(tl.touched(EVENTS, accs, t - tl.MARGIN_S - 1, t + tl.CAP_S), [], q["id"])
            since_load = tl.touched(EVENTS, accs, d * tl.BLOCK_S, t + tl.CAP_S)
            if q["kind"] == "changed":
                self.assertTrue(since_load and fresh_questions.differs(q["answer"], q["warehouse_answer"]), q["id"])
            else:
                self.assertEqual((since_load, q["answer"]), ([], q["warehouse_answer"]), q["id"])

    def test_money_answers_are_unsigned(self):
        # answer_check reads unsigned numbers only, so a negative answer could never be found
        for q in self.qs:
            for spec in (q["answer"], q["warehouse_answer"]):
                self.assertTrue(spec[0] != "money" or spec[1] >= 0, q["id"])

    def test_every_question_states_the_time(self):
        for q in self.qs:
            self.assertTrue(q["text"].startswith(f"Current time: {q['sim_time']} IST. "))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class CrossSystemQuestionTests(FreshQuestionTests):
    qset, frozen, kinds, ids = "xs", "freshness_questions_xs.json", {"changed": 14, "control": 6, "portfolio": 4}, 25

    def test_every_question_needs_core_banking_and_another_system(self):
        for q in self.qs:
            self.assertIn("core_banking", q["systems"], q["id"])
            self.assertGreater(len(q["systems"]), 1, q["id"])


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class FreshCheckTests(unittest.TestCase):
    q = {"answer": ["count", 1], "warehouse_answer": ["count", 0]}

    def test_dates_and_times_supply_no_numbers(self):
        self.assertEqual(fresh_check.verdict(self.q, "None as of 2026-07-01 08:13 IST."), "other")
        self.assertEqual(fresh_check.verdict(self.q, "1 transaction (2026-07-01)."), "correct")

    def test_a_warehouse_answer_is_stale(self):
        q = {"answer": ["ids", ["TXN2"]], "warehouse_answer": ["ids", ["TXN1"]]}
        self.assertEqual(fresh_check.verdict(q, "It was TXN1."), "stale")
        self.assertEqual(fresh_check.verdict(q, "It was TXN2; before that TXN1."), "correct")

    def test_disclosure(self):
        self.assertTrue(fresh_check.discloses("₹1,500 as of the last nightly load"))
        self.assertTrue(fresh_check.discloses("That is the last time the data was loaded"))
        self.assertTrue(fresh_check.discloses("This uses data loaded on 2026-07-03 at 00:00 IST."))
        self.assertTrue(fresh_check.discloses("These figures are from the warehouse load at 2026-07-03 00:00 IST"))
        self.assertTrue(fresh_check.discloses("₹5,65,700, based on the last data load at 2026-07-05 00:00 IST"))
        self.assertTrue(fresh_check.discloses("This uses the latest data, loaded today (4 July 2026) at 00:00 IST."))
        self.assertFalse(fresh_check.discloses("The balance is ₹1,500."))


if __name__ == "__main__":
    unittest.main()
