# Sales agent demo: six live systems vs Tapstate

A 5 to 10 minute demo built on the manufacturing benchmark. One customer question needs all six
source systems; the same agent answers it twice, once with six connection strings and once with one
connection to Tapstate's consolidated state, and the audience sees every round trip, query and token.
Results of the rehearsals and the recorded takes: [results/RESULTS.md](results/RESULTS.md).

| File | What it does |
|---|---|
| `demo_question.py` | The customer question and its answer, computed from the raw AdventureWorks tables. Writes `query101/` into both DAB datasets. |
| `sales_agent.py` | Runs the agent once and prints its work live: round trip, tool, system, query, data returned, tokens so far. Saves the run under `runs/`. `--replay` re-prints a saved run; `--lang zh` prints everything in Simplified Chinese. |
| `trace_report.py` | One HTML page comparing two saved runs: answers, tokens, round trips, queries per system, a token curve and every call. `--lang zh` for Chinese. |
| `i18n.py` | The display strings, English and Simplified Chinese. |
| `product_state.example.tap.yml` | The Tapstate pipeline shown on the slide: a simplified `mfg_products` (below). |
| `recording/` | Records the demo as a video (below). |
| `results/` | The kept runs, trace reports and [RESULTS.md](results/RESULTS.md). |

The question (Rural Cycle Emporium reorders the bike it bought most since July 2024) needs ERP
(orders), PLM (name, bill of materials, bought-in parts), costing (list price), WMS (stock), MES
(units built in Q2 2025) and procurement (vendor lead times). Expected answer: Mountain-200 Black,
38; $2,294.99; 188 in stock; 361 built; 19 days.

## The systems

Setup A's six systems run on three engines, each system keeping its own product-key format:

| System | Engine | Database | Product key |
|---|---|---|---|
| ERP sales | Postgres | `mfg_erp` | `BK-M68B-38` |
| PLM | Postgres | `mfg_plm` | `BK-M68B-38` |
| Costing | Postgres | `mfg_costing` | `BK-M68B-38` |
| MES | MySQL 8 | `mfg_mes` | `bk-m68b-38` |
| Procurement | MySQL 8 | `mfg_procurement` | `BK_M68B_38` |
| WMS | MongoDB (its own server) | `mfg_wms` | `AW:BK-M68B-38` |

Tapstate reads those same six databases (Postgres logical replication, the MySQL binlog, MongoDB
change streams) and keeps setup C, the MongoDB database `mfg_state`, in its own store. There is no
copy in between.

## Prepare (dev machine, once)

Set up the benchmark first ([../README.md](../README.md#reproduce-it)): stack up, sources split and
loaded (`dab/split_sources.py`), Tapstate pipelines run and verified (`dab/run_tapstate.sh`, then
`dab/verify_tapstate.py --wait --install`). `$DAB` below is your DataAgentBench checkout. Then:

```sh
. projects/manufacturing-state-bench/env.sh
python projects/manufacturing-state-bench/dab/make_queries.py "$DAB"     # if not already run
python projects/manufacturing-state-bench/demo/demo_question.py "$DAB"   # adds query101
```

`make_queries.py` removes query directories it does not own, so re-run `demo_question.py` after it.

## Rehearse (paid model runs)

```sh
cd projects/manufacturing-state-bench/demo
python sales_agent.py "$DAB" --setup A      # six live systems
python sales_agent.py "$DAB" --setup C      # Tapstate
python trace_report.py runs/A_<ts> runs/C_<ts> -o trace_report.html
```

Add `--lang zh` to both for a Chinese audience. It changes only what is printed: the agent's prompt,
its queries and the saved runs stay in English, so the numbers are comparable. Run each setup about
three times and keep the median runs as the backup; their numbers fill the summary slide.

## On stage

1. Story slide, then `sales_agent.py --setup A`: the six connection strings print first, then each
   call as it happens.
2. Tapstate: the six source definitions (`dab/tapstate/source/`), `tapstate status`, one document
   from `mfg_state.products`. Then `sales_agent.py --setup C`.
3. Open `trace_report.html` for the two live runs, then the summary slide.

If the network or a run misbehaves, `python sales_agent.py --replay runs/A_<ts>/stream.jsonl`
re-prints a rehearsal run at reading pace (`--lang zh` works here too).

## The Tapstate pipeline on the slide

[`product_state.example.tap.yml`](product_state.example.tap.yml) is `dab/tapstate/pipeline/mfg_products.tap.yml`
cut down to its core: one document per product, nested from the PLM product master with its
subcategory, prices and price history (costing), bill of materials, stock (WMS) and vendor offers
(procurement). It validates against the workspace's sources:

```sh
mkdir -p /tmp/dsl/source /tmp/dsl/pipeline
cp ../dab/tapstate/source/*.yml /tmp/dsl/source/ && cp product_state.example.tap.yml /tmp/dsl/pipeline/
"$TAPSTATE_CLI" validate /tmp/dsl
```

The full pipeline adds number conversions, the category join, the cost history, the model and a
final shaping step; stock and vendor offers reach it through staging views that map the WMS and
procurement keys back to the product number ([../SOURCE_SPLIT.md](../SOURCE_SPLIT.md) §3).

## Record the demo

`recording/` turns the demo into a silent 1920×1080 MP4, without ffmpeg or asciinema: util-linux
`script` records the terminal, and headless Chrome replays it on a canvas and encodes H.264.

```sh
cd projects/manufacturing-state-bench/demo
export TAPSTATE_CLI=/path/to/tapstate                 # the CLI, for the Tapstate segment
bash recording/record_live.sh "$DAB" [zh]             # live: run A, the Tapstate segment, run C (2 paid runs)
python recording/make_video.py "$DAB" [--lang zh]     # title card, the three segments, the report, a closing card
python recording/make_video.py "$DAB" --sample [--lang zh]   # a short clip to check the look first
```

| File | What it does |
|---|---|
| `record_live.sh` | Records the three live segments (`*.out`, `*.timing`), English or Chinese. |
| `tapstate_side.sh`, `show_product.js` | The Tapstate segment: source definitions, pipeline status, one product document. |
| `make_video.py` | Builds the timeline (cards, terminal replays with idle gaps capped at 2 s, the trace report scrolled with two queries opened), renders it and writes one still per section to `stills/`. |
| `player.html`, `cdp.py` | The canvas player and a minimal Chrome DevTools client. Chinese text uses Noto Sans SC (OFL), which `make_video.py` downloads once into `recording/fonts/` (sha256-checked). |
| `remux_mp4.py` | Rewrites Chrome's fragmented MP4 as a regular one (real duration, seekable in QuickTime and PowerPoint). |
| `live_change.py` | The live change, measured: by default the bike's list price in costing (Postgres; `stock` for the WMS in MongoDB), changed and set back, timed until Tapstate's live document shows it, then `verify_tapstate.py`. Writes `live_change.json`. |
| `build_section.py` | The "how the state is built" section: the flow drawn from `dab/tapstate/` (one straight lane per source, converging only into the nest pipelines), the pipeline beside its document, and the split screen rendered from `live_change.json`. `make_video.py --sample-build [--name N]` renders it alone. |

Videos, recordings and stills stay out of git.

Disclosure for the audience: the six systems run on three engines (Postgres for ERP, PLM and
costing; MySQL for MES and procurement; MongoDB for WMS, on its own server), and Tapstate captures
changes from those same six databases, the ones the agent in setup A queries. The data is loaded
once and does not change during the demo.
