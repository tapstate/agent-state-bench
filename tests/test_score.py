"""Aggregation and confidence intervals of the scorer, on small hand-checkable run sets."""
import random

from score import bootstrap_ci, cost_per_correct, pass_at_1, summarize


def run(query, valid, cost=0.10, tokens=1000, calls=3):
    return {"query": query, "valid": valid, "strict": valid, "cost_usd": cost,
            "input_tokens": tokens, "output_tokens": 0, "llm_calls": calls, "duration_s": 10.0}


SAMPLE = [run("q1", True), run("q1", True), run("q1", False),
          run("q2", False, cost=0.30), run("q2", False, cost=0.30), run("q2", True, cost=0.30)]


def test_pass_at_1_averages_per_question():
    # q1: 2/3, q2: 1/3 -> mean 0.5 (a flat run average would also be 0.5 here, so check an uneven set)
    assert abs(pass_at_1(SAMPLE) - 0.5) < 1e-9
    uneven = SAMPLE + [run("q3", True)]
    assert abs(pass_at_1(uneven) - (2 / 3 + 1 / 3 + 1) / 3) < 1e-9


def test_cost_per_correct_counts_failed_runs():
    assert abs(cost_per_correct(SAMPLE) - (3 * 0.10 + 3 * 0.30) / 3) < 1e-9


def test_summary_moves_when_an_answer_flips():
    before = summarize(SAMPLE, n_boot=200)
    after = summarize([dict(SAMPLE[2], valid=True)] + SAMPLE[:2] + SAMPLE[3:], n_boot=200)
    assert after["pass@1"] > before["pass@1"]
    assert after["usd_per_correct"] < before["usd_per_correct"]


def synthetic(n_questions, p=0.7, runs=5, seed=1):
    rng = random.Random(seed)
    return [run(f"q{i}", rng.random() < p) for i in range(n_questions) for _ in range(runs)]


def test_interval_covers_the_true_rate():
    lo, hi = bootstrap_ci(synthetic(40), pass_at_1, n=2000)
    assert lo <= 0.7 <= hi


def test_interval_narrows_with_more_questions():
    lo1, hi1 = bootstrap_ci(synthetic(20), pass_at_1, n=2000)
    lo4, hi4 = bootstrap_ci(synthetic(80), pass_at_1, n=2000)
    assert (hi4 - lo4) < (hi1 - lo1)
