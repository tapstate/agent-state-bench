# Sales agent demo: six live systems vs Tapstate

A 5 to 10 minute demo built on the manufacturing benchmark. One customer question needs all six
source systems; the same agent answers it twice, once with six connection strings and once with one
connection to Tapstate's consolidated state, and the audience sees every round trip, query and token.

| File | What it does |
|---|---|
| `demo_question.py` | The customer question and its answer, computed from the raw AdventureWorks tables. Writes `query101/` into both DAB datasets. |
| `sales_agent.py` | Runs the agent once and prints its work live: round trip, tool, system, query, data returned, tokens so far. Saves the run under `runs/`. `--replay` re-prints a saved run. |
| `trace_report.py` | One HTML page comparing two saved runs: answers, tokens, round trips, queries per system, a token curve and every call. |

The question (Rural Cycle Emporium reorders the bike it bought most since July 2024) needs ERP
(orders), PLM (name, bill of materials, bought-in parts), costing (list price), WMS (stock), MES
(units built in Q2 2025) and procurement (vendor lead times). Expected answer: Mountain-200 Black,
38; $2,294.99; 188 in stock; 361 built; 19 days.

## Prepare (dev machine, once)

Set up the benchmark first (see `../SOURCE_SPLIT.md` and the scripts in `../dab/`): stack up, sources
split and loaded, Tapstate pipelines run and verified (`dab/run_tapstate.sh`, then
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

Run each setup about three times and keep the median runs as the backup recording; their numbers
fill the summary slide.

## On stage

1. Story slide, then `sales_agent.py --setup A`: the six connection strings print first, then each
   call as it happens.
2. Tapstate: the six source definitions (`dab/tapstate/source/`), `tapstate status`, one document
   from `mfg_state.products`. Then `sales_agent.py --setup C`.
3. Open `trace_report.html` for the two live runs, then the summary slide.

If the network or a run misbehaves, `python sales_agent.py --replay runs/A_<ts>/stream.jsonl`
re-prints a rehearsal run at reading pace.

Disclosure for the audience: in this stack Tapstate captures changes from a Postgres copy of the six
systems (one schema per system, same values and key formats), loaded by
`dab/load_mfg_to_postgres.py`. The agent in setup A queries the six systems on their own engines.
