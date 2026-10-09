"""Self-checks of the bank DAB datasets. No databases or model calls; needs dab/data/db.json
(python fetch_data.py). Run from dab/: python -m unittest discover -s tests -v
"""
import copy
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import common  # noqa: E402

HAVE_DATA = common.DB_JSON.exists()
if HAVE_DATA:
    import answer_check  # noqa: E402
    import build_consolidated  # noqa: E402
    import questions  # noqa: E402
    import split_sources  # noqa: E402

    CUSTOMERS = common.load_customers()


def in_memory_sources(customers):
    """What build_consolidated.read_sources returns, built from split_sources.rows without databases."""
    r = split_sources.rows(customers)

    def dicts(name, ddl):
        cols = split_sources.columns(ddl)
        return [dict(zip(cols, row)) for row in r[name]]

    cbs = {t: dicts(t, ddl) for t, ddl in split_sources.CBS_TABLES.items()}
    ft = split_sources.FILE_TABLES
    cards = {t: dicts(t, ft["cards.db"][t]) for t in ("cards", "card_controls")}
    mandates = dicts("mandates", ft["payments.db"]["mandates"])
    loans = dicts("loans", ft["lending.duckdb"]["loans"])
    policies = dicts("policies", ft["insurance.duckdb"]["policies"])
    crm = {"customers": r["crm_customers"],
           "service_requests": [{k: v for k, v in d.items() if k != "_id"} for d in r["service_requests"]],
           "offers": [{k: v for k, v in d.items() if k != "_id"} for d in r["offers"]]}
    return cbs, cards, mandates, loans, policies, crm


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class KeyFormatTests(unittest.TestCase):
    def test_every_key_transform_is_reversible_and_unique(self):
        cids = list(CUSTOMERS)
        accs = [a for c in CUSTOMERS.values() for a in c["accounts"]]
        for f, inv in ((common.lending_borrower_ref, common.lending_customer_id),
                       (common.crm_customer_id, common.crm_to_customer_id),
                       (common.insurance_policyholder_ref, common.insurance_customer_id)):
            self.assertEqual([inv(f(c)) for c in cids], cids)
            self.assertEqual(len({f(c) for c in cids}), len(cids))
        self.assertEqual(len({common.cards_account_no(a) for a in accs}), len(accs))
        self.assertEqual(common.payments_account_id(common.payments_debit_account("OURB0105821", accs[0])), accs[0])


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class ConsolidationTests(unittest.TestCase):
    def test_split_then_consolidate_reproduces_upstream(self):
        built = build_consolidated.consolidate(*in_memory_sources(copy.deepcopy(CUSTOMERS)))
        self.assertEqual(build_consolidated.verify(built, CUSTOMERS), [])

    def test_a_changed_value_is_reported(self):
        sources = in_memory_sources(copy.deepcopy(CUSTOMERS))
        sources[3][0]["outstanding"] += 1          # one loan's balance
        problems = build_consolidated.verify(build_consolidated.consolidate(*sources), CUSTOMERS)
        self.assertEqual(len(problems), 1)
        self.assertIn("outstanding", problems[0])

    def test_a_key_in_the_wrong_format_is_refused(self):
        sources = in_memory_sources(copy.deepcopy(CUSTOMERS))
        sources[3][0]["borrower_ref"] = "CUST_" + sources[3][0]["borrower_ref"]   # not the lending format
        with self.assertRaises(SystemExit) as e:
            build_consolidated.consolidate(*sources)
        self.assertIn("could not be attached", str(e.exception))

    def test_card_ids_collide_across_customers_but_not_within_one(self):
        ids = [k for c in CUSTOMERS.values() for k in c["cards"]]
        self.assertGreater(len(ids), len(set(ids)))
        for c in CUSTOMERS.values():
            self.assertEqual(len(c["cards"]), len(set(c["cards"])))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class AnswerCheckTests(unittest.TestCase):
    h = staticmethod(answer_check.holds) if HAVE_DATA else None

    def test_money_accepts_indian_grouping_and_rupee_signs(self):
        spec = ["money", 462482291.67, 1.0]
        for text in ("₹46,24,82,291.67", "Rs.462482291.67", "Total: 462,482,291.67 INR", "about 462482291"):
            self.assertTrue(self.h(spec, text), text)
        self.assertFalse(self.h(spec, "462482293.00"))

    def test_count_needs_a_standalone_number(self):
        self.assertTrue(self.h(["count", 53], "There are 53 such RDs."))
        self.assertFalse(self.h(["count", 53], "CARD53 and 530"))

    def test_ids_must_all_appear_and_naming_an_excluded_one_is_fine(self):
        spec = ["ids", ["MND1", "MND2"]]
        self.assertTrue(self.h(spec, "MND1, MND2"))
        self.assertFalse(self.h(spec, "MND1"))
        self.assertTrue(self.h(spec, "MND1 and MND2; I left out MND3 because it is paused."))
        self.assertFalse(self.h(spec, "MND12, MND2"))

    def test_labeled_reads_the_value_up_to_the_next_label(self):
        spec = ["labeled", [["cards", ["count", 1]], ["mandates", ["count", 3]]]]
        self.assertTrue(self.h(spec, "accounts: 2, cards: 1, mandates: 3"))
        self.assertFalse(self.h(spec, "accounts: 1, cards: 2, mandates: 3"))

    def test_labeled_accepts_a_table_row(self):
        spec = ["labeled", [["HNI", ["money", 279643333.35, 1.0]], ["MASS", ["money", 65770833.34, 1.0]]]]
        self.assertTrue(self.h(spec, "| HNI | 80 | 279,643,333.35 |\n| MASS | 183 | 65,770,833.34 |"))
        self.assertFalse(self.h(spec, "| HNI | 80 | 65,770,833.34 |\n| MASS | 183 | 279,643,333.35 |"))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class QuestionTests(unittest.TestCase):
    def test_frozen_questions_match_the_data(self):
        """questions.json is the frozen record; a change to questions.py or the data must be deliberate."""
        frozen = json.loads((HERE / "questions.json").read_text())
        self.assertEqual(json.loads(json.dumps(questions.build(CUSTOMERS))), frozen)

    def test_pilot_composition(self):
        qs = questions.build(CUSTOMERS)
        self.assertEqual(len(qs), 24)
        cats = [q["category"] for q in qs]
        self.assertEqual([cats.count(c) for c in (questions.CONTROL, questions.TWO, questions.MULTI,
                                                  questions.PORTFOLIO)], [4, 8, 8, 4])

    def test_no_trivial_answers(self):
        def flat(spec):
            if spec[0] in ("all",):
                return [x for s in spec[1] for x in flat(s)]
            if spec[0] == "labeled":
                return [x for _, s in spec[1] for x in flat(s)]
            return [spec]
        for q in questions.build(CUSTOMERS):
            for s in flat(q["answer"]):
                if s[0] in ("count", "money"):
                    self.assertNotEqual(s[1], 0, f"question {q['id']} has a zero answer")

    def test_customer_scoped_questions_avoid_colliding_ids(self):
        bad = questions.colliding_ids(CUSTOMERS)
        for q in questions.build(CUSTOMERS):
            self.assertFalse({w.strip(".,") for w in q["text"].split()} & bad, q["id"])


if __name__ == "__main__":
    unittest.main()
