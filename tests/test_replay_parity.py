"""The export parity check: same rows in any order and representation pass, a missing row fails."""
import partb_crm as P

NAMES = ["Id", "Name", "Qty"]
ROWS = [("a1", "Acme", 2), ("b2", "Beta", None), ("c3", "Core", 5)]


def test_same_rows_in_another_order_and_type_match():
    shuffled = [("c3", "Core", "5"), ("a1", "Acme", "2"), ("b2", "Beta", None)]
    assert P.digest(NAMES, ROWS) == P.digest(NAMES, shuffled)


def test_a_missing_or_changed_row_is_caught():
    assert P.digest(NAMES, ROWS) != P.digest(NAMES, ROWS[:2])
    assert P.digest(NAMES, ROWS) != P.digest(NAMES, [("a1", "Acme", 3)] + ROWS[1:])
