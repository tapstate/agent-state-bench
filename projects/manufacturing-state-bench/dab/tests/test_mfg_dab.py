"""Self-checks of the manufacturing DAB datasets. No databases or model calls; needs the AdventureWorks
files (python fetch_data.py). Run from dab/: python -m unittest discover -s tests -v
"""
import copy
import json
import re
import sys
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import aw  # noqa: E402
import common  # noqa: E402

HAVE_DATA = (aw.RAW / "instawdb.sql").exists()
if HAVE_DATA:
    import answer_check  # noqa: E402
    import build_consolidated  # noqa: E402
    import questions  # noqa: E402
    import split_sources  # noqa: E402

    SYSTEMS = split_sources.systems()
    REFERENCE = common.reference_state()
    DATA = questions.Data()


def in_memory_sources(systems):
    """What build_consolidated.read_sources returns, from split_sources.systems() without databases."""
    out = {}
    for name, tables in systems.items():
        if name == "wms":
            out.update(copy.deepcopy(tables))
            continue
        for t, (cols, rows, _) in tables.items():
            out[t] = [dict(zip([c for c, _ in cols], r)) for r in rows]
    return out


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class KeyFormatTests(unittest.TestCase):
    def test_every_product_key_format_is_reversible_and_unique(self):
        numbers = [p["ProductNumber"] for p in aw.table("Product").to_dict("records")]
        common.assert_reversible(numbers)
        for f in (common.mes_part_no, common.wms_item, common.proc_part_ref):
            self.assertEqual(len({f(n) for n in numbers}), len(numbers))
            self.assertTrue(all(f(n) != n for n in numbers), f.__name__)

    def test_no_integer_product_id_reaches_the_sources(self):
        cols = {c for s, tables in SYSTEMS.items() if s != "wms" for _, (cs, _, _) in tables.items() for c, _ in cs}
        self.assertNotIn("product_id", cols)
        self.assertTrue(all(d["item"].startswith("AW:") for d in SYSTEMS["wms"]["stock"]))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class ConsolidationTests(unittest.TestCase):
    def test_split_then_consolidate_reproduces_the_reference(self):
        built = build_consolidated.consolidate(in_memory_sources(SYSTEMS))
        self.assertEqual(common.verify(built, REFERENCE), [])
        self.assertEqual({c: len(built[c]) for c in common.COLLECTIONS},
                         {"products": 504, "work_orders": 72591, "sales_orders": 31465, "purchase_orders": 4012})

    def test_a_changed_value_is_reported(self):
        src = in_memory_sources(SYSTEMS)
        src["work_orders"][0]["scrapped_qty"] += 1
        problems = common.verify(build_consolidated.consolidate(src), REFERENCE)
        self.assertEqual(len(problems), 1)
        self.assertIn("scrapped_qty", problems[0])

    def test_a_key_in_the_wrong_format_is_refused(self):
        src = in_memory_sources(SYSTEMS)
        src["work_orders"][0]["part_no"] = common.proc_part_ref(src["work_orders"][0]["part_no"].upper())
        with self.assertRaises(SystemExit) as e:
            build_consolidated.consolidate(src)
        self.assertIn("could not be attached", str(e.exception))

    def test_reference_embeds_every_child_row(self):
        r = REFERENCE
        self.assertEqual(sum(len(d["lines"]) for d in r["sales_orders"].values()), 121317)
        self.assertEqual(sum(len(d["routings"]) for d in r["work_orders"].values()), 67131)
        self.assertEqual(sum(len(d["lines"]) for d in r["purchase_orders"].values()), 8845)
        self.assertEqual(sum(len(d["inventory"]) for d in r["products"].values()), 1069)
        self.assertEqual(sum(len(d["vendors"]) for d in r["products"].values()), 460)
        # level-0 BOM rows name no assembly and are the only ones left out (SOURCE_SPLIT.md §3)
        self.assertEqual(sum(len(d["bom"]) for d in r["products"].values()), 2679 - 103)


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class AnswerCheckTests(unittest.TestCase):
    h = staticmethod(answer_check.holds) if HAVE_DATA else None

    def test_money_accepts_commas_and_dollar_signs(self):
        spec = ["money", 9121931.65, 1.0]
        for text in ("$9,121,931.65", "Total: 9121931.65 USD", "about 9,121,931"):
            self.assertTrue(self.h(spec, text), text)
        self.assertFalse(self.h(spec, "9121933.00"))

    def test_count_is_not_read_out_of_a_product_number(self):
        self.assertTrue(self.h(["count", 347], "347 units"))
        self.assertFalse(self.h(["count", 347], "PD-R347"))

    def test_product_numbers_as_ids(self):
        spec = ["ids", ["PD-R347", "SE-M798"]]
        self.assertTrue(self.h(spec, "PD-R347 and SE-M798"))
        self.assertFalse(self.h(spec, "PD-R3470 and SE-M798"))

    def test_labeled_accepts_a_table_row(self):
        spec = ["labeled", [["Bikes", ["count", 58]], ["Components", ["count", 886]]]]
        self.assertTrue(self.h(spec, "| Bikes | 58 |\n| Components | 886 |"))
        self.assertFalse(self.h(spec, "| Bikes | 886 |\n| Components | 58 |"))


@unittest.skipUnless(HAVE_DATA, "run fetch_data.py first")
class QuestionTests(unittest.TestCase):
    def test_frozen_questions_match_the_data(self):
        """questions.json is the frozen record; a change to questions.py or the data must be deliberate."""
        frozen = json.loads((HERE / "questions.json").read_text())
        self.assertEqual(json.loads(json.dumps(questions.build(DATA))), frozen)

    def test_composition(self):
        qs = questions.build(DATA)
        self.assertEqual(len(qs), 20)
        cats = [q["category"] for q in qs]
        self.assertEqual([cats.count(c) for c in (questions.CONTROL, questions.TWO, questions.MULTI,
                                                  questions.PORTFOLIO)], [4, 6, 6, 4])
        for q in qs:
            want = {questions.CONTROL: 1, questions.TWO: 2}.get(q["category"])
            if want:
                self.assertEqual(len(q["systems"]), want, q["id"])
            elif q["category"] == questions.MULTI:
                self.assertGreaterEqual(len(q["systems"]), 3, q["id"])

    def test_no_trivial_answers(self):
        def flat(spec):
            if spec[0] == "all":
                return [x for s in spec[1] for x in flat(s)]
            if spec[0] == "labeled":
                return [x for _, s in spec[1] for x in flat(s)]
            return [spec]
        for q in questions.build(DATA):
            for s in flat(q["answer"]):
                if s[0] in ("count", "money"):
                    self.assertNotEqual(s[1], 0, f"question {q['id']} has a zero answer")

    def test_every_validator_accepts_its_reference_answer(self):
        from make_queries import gt_lines
        for q in questions.build(DATA):
            spec = json.loads(json.dumps(q["answer"]))
            self.assertTrue(answer_check.holds(spec, "\n".join(gt_lines(spec))), q["id"])


class TapstateWorkspaceTests(unittest.TestCase):
    """The workspace's wiring, read from its YAML; Tapstate itself checks the rest on apply."""
    WS = HERE / "tapstate"

    def load(self, kind):
        return {d["id"]: d for d in (yaml.safe_load(p.read_text()) for p in sorted((self.WS / kind).glob("*.yml")))}

    def test_every_pipeline_reads_declared_sources_and_writes_one_view(self):
        sources, pipelines = self.load("source"), self.load("pipeline")
        self.assertEqual(len(pipelines), 10)
        for pid, p in pipelines.items():
            self.assertTrue(set(p["source"]) <= set(sources), pid)
            self.assertIn("view", p, pid)

    def test_views_read_back_are_written_by_a_pipeline(self):
        sources, pipelines = self.load("source"), self.load("pipeline")
        views = {p["view"]["id"] for p in pipelines.values()}
        for sid in ("src_mfg_ref", "src_mfg_stage"):
            self.assertTrue(set(sources[sid]["tables"]) <= views, sid)
        from verify_tapstate import VIEWS
        self.assertTrue(set(VIEWS.values()) <= views)

    def test_no_many_to_one_embed_under_an_array(self):
        """Tapstate 0.7.0 attaches such an embed to one element of the whole level (SOURCE_SPLIT.md §3)."""
        def walk(embeds, under_array):
            for e in embeds or []:
                if under_array:
                    self.fail(f"embed {e['path'] if 'path' in e else e['from']} sits under an array")
                walk(e.get("embed"), e.get("as") == "array")
        for pid, p in self.load("pipeline").items():
            for t in p["transforms"]:
                if t["type"] == "nest":
                    walk(t["root"].get("embed"), False)


class HarnessPatchTests(unittest.TestCase):
    def test_dab_runner_knows_both_datasets(self):
        patch = (HERE.parents[2] / "harness" / "dab.patch").read_text()
        for ds in ("manufacturing", "manufacturing_consolidated"):
            self.assertTrue(re.search(rf'^\+\s+"{ds}",$', patch, re.M), ds)


if __name__ == "__main__":
    unittest.main()
