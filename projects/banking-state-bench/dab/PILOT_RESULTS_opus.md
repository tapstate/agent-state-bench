Model cc-claude-opus-5-5; 360 scored runs.

### all questions (24 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| A | 120 | 1.000 | 1.00–1.00 | 79,358 | 3.6× | 7 | 26 | 0.172 |
| B | 120 | 0.992 | 0.97–1.00 | 74,508 | 3.4× | 7 | 24 | 0.162 |
| C | 120 | 0.992 | 0.97–1.00 | 22,074 | 1.0× | 4 | 20 | 0.057 |

### single-source control (4 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| A | 20 | 1.000 | 1.00–1.00 | 20,961 | 1.0× | 4 | 17 | 0.045 |
| B | 20 | 1.000 | 1.00–1.00 | 23,581 | 1.1× | 4 | 18 | 0.046 |
| C | 20 | 1.000 | 1.00–1.00 | 20,830 | 1.0× | 4 | 17 | 0.039 |

### two sources (8 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| A | 40 | 1.000 | 1.00–1.00 | 74,419 | 3.5× | 6 | 25 | 0.156 |
| B | 40 | 1.000 | 1.00–1.00 | 73,480 | 3.4× | 6 | 22 | 0.140 |
| C | 40 | 0.975 | 0.93–1.00 | 21,558 | 1.0× | 4 | 19 | 0.047 |

### three or more sources (8 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| A | 40 | 1.000 | 1.00–1.00 | 88,900 | 3.6× | 9 | 29 | 0.205 |
| B | 40 | 0.975 | 0.93–1.00 | 87,502 | 3.6× | 8 | 25 | 0.198 |
| C | 40 | 1.000 | 1.00–1.00 | 24,498 | 1.0× | 4 | 21 | 0.061 |

### portfolio (4 questions)

| Setup | Runs | pass@1 | 95% CI | Median tokens | vs C | Median turns | Median s | API-equiv. $ per correct |
|---|---:|---:|---|---:|---:|---:|---:|---:|
| A | 20 | 1.000 | 1.00–1.00 | 100,268 | 2.3× | 8 | 29 | 0.266 |
| B | 20 | 1.000 | 1.00–1.00 | 93,066 | 2.2× | 8 | 29 | 0.249 |
| C | 20 | 1.000 | 1.00–1.00 | 43,271 | 1.0× | 5 | 24 | 0.089 |

### Per question (pass rate over runs; median tokens)

| Q | Category | A pass | A tokens | B pass | B tokens | C pass | C tokens |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 | single-source control | 5/5 | 38,299 | 5/5 | 31,091 | 5/5 | 21,002 |
| 2 | single-source control | 5/5 | 15,163 | 5/5 | 16,712 | 5/5 | 15,015 |
| 3 | single-source control | 5/5 | 15,633 | 5/5 | 23,614 | 5/5 | 22,039 |
| 4 | single-source control | 5/5 | 21,037 | 5/5 | 29,846 | 5/5 | 20,911 |
| 5 | two sources | 5/5 | 38,370 | 5/5 | 23,123 | 5/5 | 16,589 |
| 6 | two sources | 5/5 | 84,496 | 5/5 | 60,761 | 5/5 | 21,558 |
| 7 | two sources | 5/5 | 66,274 | 5/5 | 77,469 | 4/5 | 23,836 |
| 8 | two sources | 5/5 | 28,572 | 5/5 | 24,080 | 5/5 | 16,241 |
| 9 | two sources | 5/5 | 72,025 | 5/5 | 82,189 | 5/5 | 15,158 |
| 10 | two sources | 5/5 | 86,790 | 5/5 | 81,803 | 5/5 | 21,630 |
| 11 | two sources | 5/5 | 71,816 | 5/5 | 73,609 | 5/5 | 23,516 |
| 12 | two sources | 5/5 | 80,050 | 5/5 | 80,945 | 5/5 | 23,043 |
| 13 | three or more sources | 5/5 | 107,450 | 5/5 | 95,497 | 5/5 | 21,416 |
| 14 | three or more sources | 5/5 | 26,094 | 4/5 | 33,862 | 5/5 | 17,714 |
| 15 | three or more sources | 5/5 | 80,748 | 5/5 | 79,647 | 5/5 | 30,454 |
| 16 | three or more sources | 5/5 | 89,092 | 5/5 | 88,461 | 5/5 | 47,959 |
| 17 | three or more sources | 5/5 | 126,756 | 5/5 | 105,444 | 5/5 | 25,680 |
| 18 | three or more sources | 5/5 | 71,131 | 5/5 | 77,885 | 5/5 | 24,357 |
| 19 | three or more sources | 5/5 | 103,884 | 5/5 | 120,732 | 5/5 | 28,869 |
| 20 | three or more sources | 5/5 | 44,506 | 5/5 | 26,670 | 5/5 | 16,182 |
| 21 | portfolio | 5/5 | 79,678 | 5/5 | 92,228 | 5/5 | 16,433 |
| 22 | portfolio | 5/5 | 191,015 | 5/5 | 173,921 | 5/5 | 30,257 |
| 23 | portfolio | 5/5 | 106,546 | 5/5 | 112,209 | 5/5 | 58,837 |
| 24 | portfolio | 5/5 | 75,255 | 5/5 | 68,732 | 5/5 | 51,778 |
