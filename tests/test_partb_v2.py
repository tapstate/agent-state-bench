"""Part B v2: where the lagged copies fall in the workload, and how an answer is checked."""
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "datasets" / "partb_v2"))
import questions as Q  # noqa: E402

pytest.importorskip("pymongo")
pytest.importorskip("psycopg2")
import run_partb_v2 as R  # noqa: E402


def test_copies_are_taken_in_age_order_at_the_right_distance_before_T():
    targets, at_t = R.lag_targets(2000)
    arms = [a for a, _ in targets]
    assert arms == ["S4", "S3", "S2", "S1"]                       # oldest copy first
    points = [t for _, t in targets]
    assert points == sorted(points) and points[-1] < at_t          # strictly before T
    behind = {a: at_t - t for a, t in targets}
    assert behind == {"S4": 14000, "S3": 2000, "S2": 83, "S1": 21}  # a week, a day, an hour, 15 minutes
    assert R.lag_targets(4000)[1] == 2 * at_t                      # a busier system: every lag holds more change


def checker(tmp_path, truth):
    Q.write_query_dir(tmp_path / "q", {"text": "t", "truth": truth})
    spec = importlib.util.spec_from_file_location("v", tmp_path / "q" / "validate.py")
    v = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(v)
    return lambda text: v.validate(text)[0]


def test_every_value_of_the_answer_is_required(tmp_path):
    ok = checker(tmp_path, [[4137, "no"]])
    assert ok("Order 4137 has not been delivered yet.")
    assert ok("Order 4137 - no.")
    assert not ok("Order 4137 was delivered, yes.")      # the wrong side
    assert not ok("Order 4136 has not been delivered.")  # the wrong order


def test_yes_is_not_undone_by_an_incidental_not(tmp_path):
    ok = checker(tmp_path, [["yes", 74]])
    assert ok("Yes - though not many: 74 units.")
    assert not ok("No, only 74 units.")


def test_amounts_match_to_the_cent(tmp_path):
    ok = checker(tmp_path, [["1234.50"]])
    assert ok("The balance is $1,234.50.")
    assert not ok("The balance is $1,234.60.")
