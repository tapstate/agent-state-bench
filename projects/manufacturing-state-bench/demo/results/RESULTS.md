# Sales agent demo: results

All runs on 2026-11-12 with Claude Opus 5.5 (`claude-opus-5-5`) through Claude Code, DAB's four tools
only, on the demo question (query101): Rural Cycle Emporium reorders the bike it bought most since
1 July 2024; which bike, list price, units in stock, units built in Q2 2025, longest supplier lead
time. Expected answer: Mountain-200 Black, 38; $2,294.99; 188; 361; 19 days. Every run below
answered it correctly.

Tokens are input + cache write + cache read + output, from Claude Code's final result. Costs are
Claude Code's API-equivalent estimates.

## Layouts

| Layout | Setup A: the six source systems | Setup C |
|---|---|---|
| First (`first_layout/`) | ERP, costing on Postgres; PLM on SQLite; MES, procurement on DuckDB; WMS on MongoDB | MongoDB `mfg_state`, built by Tapstate from a Postgres copy of the six systems |
| Three engines (`three_engines/`) | ERP, PLM, costing on Postgres; MES, procurement on MySQL 8; WMS on its own MongoDB | MongoDB `mfg_state`, built by Tapstate reading the six databases directly |

## Rehearsal: 3 runs per setup, medians

| Layout | Setup | Tokens | Round trips | Queries | Failed calls | Cost | Wall time | A / C tokens |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| First | A | 197,851 | 9 | 11 | 0 | $0.37 | 33.2 s | **2.4×** |
| First | C | 83,911 | 7 | 6 | 0 | $0.19 | 24.0 s | |
| Three engines | A | 210,431 | 13 | 16 | 0 | $0.34 | 42.0 s | **2.5×** |
| Three engines | C | 83,699 | 7 | 6 | 0 | $0.17 | 23.3 s | |

Median runs: first layout `A_20261112-074634`, `C_20261112-074833`
([trace_report_baseline.html](trace_report_baseline.html)); three engines `A_20261112-083938`,
`C_20261112-084246` ([trace_report_en.html](trace_report_en.html)).

All rehearsal runs:

| Layout | Run | Correct | Tokens | Round trips | Queries | Cost | Wall time |
|---|---|---|---:|---:|---:|---:|---:|
| First | A_20261112-074634 | yes | 197,851 | 9 | 11 | $0.37 | 33.2 s |
| First | A_20261112-074715 | yes | 162,339 | 9 | 11 | $0.30 | 32.8 s |
| First | A_20261112-074750 | yes | 260,067 | 11 | 14 | $0.38 | 40.2 s |
| First | C_20261112-074833 | yes | 83,911 | 7 | 6 | $0.19 | 24.0 s |
| First | C_20261112-074859 | yes | 114,138 | 7 | 7 | $0.26 | 26.4 s |
| First | C_20261112-074928 | yes | 59,086 | 6 | 5 | $0.13 | 18.4 s |
| Three engines | A_20261112-083938 | yes | 210,431 | 13 | 16 | $0.34 | 42.0 s |
| Three engines | A_20261112-084023 | yes | 279,495 | 13 | 14 | $0.36 | 46.1 s |
| Three engines | A_20261112-084111 | yes | 192,321 | 11 | 14 | $0.32 | 40.1 s |
| Three engines | C_20261112-084154 | yes | 82,662 | 7 | 4 | $0.17 | 22.6 s |
| Three engines | C_20261112-084219 | yes | 89,883 | 7 | 6 | $0.19 | 24.9 s |
| Three engines | C_20261112-084246 | yes | 83,699 | 7 | 6 | $0.17 | 23.3 s |

## Live takes for the recordings (three engines, one take each)

| Take | Setup | Run | Correct | Tokens | Round trips | Queries | Failed calls | Cost | Wall time | A / C |
|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|
| English, 09:05 | A | A_20261112-090554 | yes | 155,088 | 9 | 12 | 0 | $0.28 | 34.4 s | 1.9× |
| | C | C_20261112-090658 | yes | 82,581 | 7 | 5 | 0 | $0.17 | 22.9 s | |
| Chinese (`--lang zh`), 09:45 | A | A_20261112-094518 | yes | 295,549 | 10 | 16 | 0 | $0.51 | 43.4 s | 2.8× |
| | C | C_20261112-094631 | yes | 106,763 | 8 | 7 | 0 | $0.20 | 28.6 s | |

The Chinese take's report: [trace_report_zh.html](trace_report_zh.html). `--lang zh` changes only
what is displayed; the agent's prompt and question are the same English ones as in every other run.

## Benchmark smoke test on the three-engine layout (question 14)

Question 14: purchased Components below safety stock, with the shortest vendor lead time of each
(PLM + WMS + procurement). One run per setup (`three_engines/smoke_q14/`), against the first
layout's five runs per setup from the 200-run batch:

| Layout | Setup | Runs | Correct | Tokens (median) | Cost (median) |
|---|---|---:|---:|---:|---:|
| First | A | 5 | 5 | 94,337 | $0.25 |
| First | C | 5 | 5 | 36,944 | $0.07 |
| Three engines | A | 1 | 1 | 125,729 | $0.29 |
| Three engines | C | 1 | 1 | 39,374 | $0.09 |

The three-engine A run had one failed call: an `execute_python` error in the agent's own code,
which it fixed in its next call.

## Live change: how fast the state follows a source (2026-11-14)

`recording/live_change.py` changes one source value and watches Tapstate's live product document,
`views.mfg_product`, every 20 ms, then sets it back, runs `verify_tapstate.py` and checks the
pipelines. The demo uses the relational change (MongoDB is the state store in the demo's story):

| Source | Change | Pipelines on the way | Reached views.mfg_product after |
|---|---|---|---:|
| costing, Postgres `mfg_costing.item_prices.list_price` | 2294.99 → 2199.99 | `mfg_products` | 0.484 s |
| | 2199.99 → 2294.99 (set back) | | 0.478 s |
| WMS, MongoDB `mfg_wms.stock.quantity` (Final Assembly) | 88 → 98 | `mfg_stock`, `mfg_products` | 0.066 s |
| | 98 → 88 (set back) | | 0.087 s |

No duplicate array elements. After each run `verify_tapstate.py` passed (15 s) and all ten pipelines
were running. Postgres changes arrive through logical replication, which Tapstate polls, hence the
longer time than MongoDB's change stream. Setup C's `mfg_state` is a
verified copy of these views and does not change with them.

**Finding: updates from MongoDB need pre-images.** The first attempt failed: `mfg_stock` stopped with
`nest.reference-tracking-requires-before-image`, because a MongoDB change stream carries an update's
earlier document only when the collection records pre-images and the Tapstate source asks for them.
The initial load (inserts only) works without them, so the build and the benchmark never hit it.
With `changeStreamPreAndPostImages` on the WMS collections and the staging views, and
`preImage: true` on the three MongoDB sources, the change went through as above
([../../SOURCE_SPLIT.md](../../SOURCE_SPLIT.md) §3).

## Tapstate's web UI

The 0.7.0 server serves a web UI on its port (sign in at `/`; English, Simplified and Traditional
Chinese). Its pipeline editor shows each pipeline as a graph (the `mfg_products` graph:
`recording/stills/builder_mfg_products.png`, not committed), and "Create pipeline" offers four build
modes: a step-by-step pipeline builder, DuckDB SQL, a visual DAG designer, and "Build with AI"
(generate a pipeline from a prompt). With the `.tap.yml` DSL and the CLI, that covers AI-assisted,
visual and scripted (automated) creation.

## What the traces show

Setup C's runs are steady (83–107k tokens on the three-engine layout): a few MongoDB aggregations on
`mfg_state`, a `$graphLookup` or a Python walk over the bill of materials, and dates written as
`{"$date": ...}` from the description, with no date errors. Setup A's cost swings with how fast the
agent works out each system's product-key format. On the three-engine layout MySQL compares text
case-sensitively (`utf8mb4_0900_bin`, chosen so keys behave as in Postgres), so `LIKE '%M68B-38%'`
finds none of the MES's lower-case part numbers; on DuckDB the agent had used `ILIKE`, which MySQL
lacks, and it now probes the MES two to six times before matching on `LOWER(part_no)`. Procurement's
`_` keys defeated a `REGEXP` over dashed numbers in one run, and the agent often pulls the whole
`vendor_items` table (about 12k characters) to match keys in Python. No run answered wrongly.

## Files

- `first_layout/<run>/`, `three_engines/<run>/`: each run's `summary.json` (tokens, round trips,
  queries per system, cost, answer, check, every call) and `tool_calls.jsonl` (from the tool
  server). `three_engines/smoke_q14/`: the smoke test's `claude_result.json`, `tool_calls.jsonl`
  and `answer.json`. Local paths are replaced by `<dab>` and `<home>`. Claude Code's raw stream,
  prompts, MCP configs and stderr are left out.
- `trace_report_en.html`, `trace_report_zh.html`, `trace_report_baseline.html`: the side-by-side
  reports (`../trace_report.py`).

## Videos (not in git)

Rendered by `../recording/make_video.py` from the recordings of `../recording/record_live.sh`, as
1920×1080 H.264 MP4 without audio:

| File | Duration | Size | Content |
|---|---:|---:|---|
| `sales_agent_demo_zh.mp4` | 4 min 33 s | 26.8 MB | the full demo in Simplified Chinese: the 09:45 live take, the Tapstate segment, the trace report, the numbers |
| `sample.mp4` | 16.5 s | 1.5 MB | look-and-feel sample (English) |
| `sample_zh.mp4` | 30.6 s | 3.3 MB | look-and-feel sample (Chinese), from replays of the 09:05 take |
| `sample_build_zh.mp4` | 67.9 s | 4.2 MB | the "how the state is built" section (Chinese): the flow drawn from the workspace, the pipeline beside its document, the measured live change |

To regenerate: see [../README.md](../README.md#record-the-demo). The full English video was not
rendered; `make_video.py` builds it from the 09:05 recordings.

## Reproduce

[../README.md](../README.md) has the setup, the run commands and the recording steps.
