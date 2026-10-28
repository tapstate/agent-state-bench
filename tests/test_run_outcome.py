"""A run's JSON result is sorted into finished / retry later / stop now.

An expired login must stop the batch: before this split it was read as a usage limit, and the
batch waited and retried for an hour without any chance of succeeding.
"""
import pytest

from run_claude_arms import AUTH_FAILED, FINISHED, NOT_RUN, classify

CASES = [
    ({"is_error": True, "num_turns": 1,
      "result": "Failed to authenticate: OAuth session expired and could not be refreshed"}, AUTH_FAILED),
    ({"is_error": True, "num_turns": 1, "result": "Invalid API key. Please run /login"}, AUTH_FAILED),
    ({"is_error": True, "num_turns": 1, "result": "Claude AI usage limit reached|1790000000"}, NOT_RUN),
    ({"is_error": True, "num_turns": 1, "result": "API Error: 529 Overloaded"}, NOT_RUN),
    ({"is_error": True, "num_turns": 0, "result": "unexpected error"}, NOT_RUN),
    ({"is_error": False, "num_turns": 7, "result": "The answer is 42."}, FINISHED),
    ({"is_error": True, "num_turns": 23, "result": "Reached max turns"}, FINISHED),
]


@pytest.mark.parametrize("res,expected", CASES)
def test_classify(res, expected):
    assert classify(res) == expected


def test_auth_failure_is_not_a_usage_limit():
    res = CASES[0][0]
    assert classify(res) != NOT_RUN
