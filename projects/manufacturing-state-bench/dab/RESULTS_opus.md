# Manufacturing benchmark: Claude Opus 5.5 results, 2026-11-12

200 runs: 20 questions × 2 setups (A: six source systems; C: Tapstate-consolidated state) × 5
runs, with `harness/run_claude_arms.py` (Claude Code 2.1.322, DAB's four tools only, 3 workers,
19 minutes), on the subscription login; costs are Claude Code's API-equivalent estimates. 200 of
200 runs completed, with no usage limits hit. Before the batch, every database was restored from
the dataset files (`reset_dbs.py`) and the model-free dry run passed. Setup C was Tapstate's output,
verified field for field against the raw-table reference before it was installed.

Raw scores: [`results/opus_scores.csv`](results/opus_scores.csv) (validator version 3). Produced by
`python harness/score.py "$DAB" --csv scores.csv` and
`python projects/manufacturing-state-bench/dab/analyze_runs.py scores.csv harness`.

Totals: setup A 7.48 million tokens and $14.74; setup C 3.45 million tokens and $6.56.

**Failures.** Four of 200 runs failed, two per setup, and all four are protocol misses: the agent's
final text holds the correct answer, but it never called `return_answer` (A: questions 3 and 16; C:
questions 12 and 17). There were no wrong answers.

**Validator correction, disclosed.** The first scoring, with the bank benchmark's checker
(version 2) unchanged, failed 7 correct answers to question 17 (5 in A, 2 in C). They put the
number before its label ("scrapped 58 Bikes units and 886 Components units"), and version 2 reads a
labeled value only after the label. Version 3 also accepts the last number before the label on the
same line (`answer_check.py`), identically for both setups; the 200 recorded answers were rescored.
The only verdicts that changed are those 7, all from fail to pass. Before the correction pass@1 was
A 0.930 and C 0.960 ([`results/opus_scores_validator_v2.csv`](results/opus_scores_validator_v2.csv)).
Token figures are unaffected.

### all questions (20 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | Mean tokens | vs C (median) | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A | 100 | 0.980 | 0.95–1.00 | 63,818 | 74,800 | 2.2× | 6 | 16 | 0.150 |
| C | 100 | 0.980 | 0.95–1.00 | 28,432 | 34,515 | 1.0× | 5 | 14 | 0.067 |

### single-source control (4 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | Mean tokens | vs C (median) | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A | 20 | 0.950 | 0.85–1.00 | 28,288 | 29,643 | 1.1× | 5 | 12 | 0.047 |
| C | 20 | 1.000 | 1.00–1.00 | 24,776 | 27,952 | 1.0× | 4 | 12 | 0.046 |

### two sources (6 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | Mean tokens | vs C (median) | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A | 30 | 1.000 | 1.00–1.00 | 57,487 | 59,506 | 2.3× | 6 | 17 | 0.117 |
| C | 30 | 1.000 | 1.00–1.00 | 25,158 | 27,323 | 1.0× | 4 | 12 | 0.048 |

### three or more sources (6 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | Mean tokens | vs C (median) | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A | 30 | 0.967 | 0.90–1.00 | 69,564 | 71,013 | 2.3× | 7 | 17 | 0.164 |
| C | 30 | 0.967 | 0.90–1.00 | 30,368 | 37,068 | 1.0× | 5 | 15 | 0.075 |

### portfolio (4 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | Mean tokens | vs C (median) | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|
| A | 20 | 1.000 | 1.00–1.00 | 133,462 | 148,580 | 3.2× | 9 | 27 | 0.280 |
| C | 20 | 0.950 | 0.85–1.00 | 42,047 | 48,039 | 1.0× | 5 | 16 | 0.106 |

### By number of source systems a question needs

| Systems | Questions | A median tokens | C median tokens | A / C | A median turns | C median turns |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 4 | 28,288 | 24,776 | 1.1× | 5 | 4 |
| 2 | 8 | 65,258 | 26,335 | 2.5× | 6 | 4 |
| 3 | 8 | 87,830 | 33,822 | 2.6× | 8 | 5 |

### Per question (pass rate over runs; median tokens)

| Q | Category | A pass | A tokens | C pass | C tokens |
|---|---|---:|---:|---:|---:|
| 1 | single-source control | 5/5 | 22,665 | 5/5 | 22,101 |
| 2 | single-source control | 5/5 | 35,054 | 5/5 | 40,353 |
| 3 | single-source control | 4/5 | 27,638 | 5/5 | 24,049 |
| 4 | single-source control | 5/5 | 33,276 | 5/5 | 25,780 |
| 5 | two sources | 5/5 | 36,924 | 5/5 | 23,757 |
| 6 | two sources | 5/5 | 36,475 | 5/5 | 30,568 |
| 7 | two sources | 5/5 | 64,777 | 5/5 | 17,368 |
| 8 | two sources | 5/5 | 48,772 | 5/5 | 25,079 |
| 9 | two sources | 5/5 | 67,070 | 5/5 | 23,772 |
| 10 | two sources | 5/5 | 88,304 | 5/5 | 30,067 |
| 11 | three or more sources | 5/5 | 63,613 | 5/5 | 24,705 |
| 12 | three or more sources | 5/5 | 47,675 | 4/5 | 70,659 |
| 13 | three or more sources | 5/5 | 88,079 | 5/5 | 25,425 |
| 14 | three or more sources | 5/5 | 94,337 | 5/5 | 36,944 |
| 15 | three or more sources | 5/5 | 44,029 | 5/5 | 30,435 |
| 16 | three or more sources | 4/5 | 75,150 | 5/5 | 28,211 |
| 17 | portfolio | 5/5 | 87,063 | 4/5 | 28,768 |
| 18 | portfolio | 5/5 | 90,102 | 5/5 | 26,587 |
| 19 | portfolio | 5/5 | 162,442 | 5/5 | 49,376 |
| 20 | portfolio | 5/5 | 136,624 | 5/5 | 91,400 |

### Observations

- **Tokens:** setup C used 2.2 times fewer tokens than A at the median (2.2 at the mean), and its
  API-equivalent cost per correct answer was 45% of A's. The gap is absent on the single-source
  controls (1.1×), 2.3× on two-source and three-or-more-source questions, and largest on the
  portfolio aggregates (3.2×), where setup A has to pull and join the most rows across databases.
  By the number of systems a question needs: 1.1× for one, 2.5× for two, 2.6× for three.
- **Where C is not cheaper:** questions 12 and 20 (margins that need the standard cost) cost C more
  than its other questions (71k and 91k median tokens), and question 12 more than A. The product
  reference embedded in sales lines carries number, name, subcategory and category, not cost, so the
  agent must combine the sales_orders and products collections itself, in two queries and Python.
  Setup A's agent did the same join across ERP and costing at similar cost.
- **Accuracy:** no difference; Opus 5.5 is at the ceiling in both setups, as in the bank benchmark.
