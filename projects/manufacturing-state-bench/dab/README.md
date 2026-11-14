# Manufacturing data as a DAB dataset

Builds the DAB datasets for the manufacturing benchmark described in [../SOURCE_SPLIT.md](../SOURCE_SPLIT.md):
Microsoft's AdventureWorks split into six source systems (setup A), the same data consolidated
into four collections (setup C), and 20 questions with validators. The agent harness is this
repository's `harness/`.

| File | What it does |
|---|---|
| `fetch_data.py` | downloads the AdventureWorks OLTP install script, checks its sha256, unzips it into `data/raw/` |
| `aw.py` | reads the script's CSV files into pandas, with column names from its `instawdb.sql` |
| `common.py` | database names, the product key format of each system and its inverse, setup C's shape, and `reference_state()`: setup C built straight from the raw tables, which everything is checked against |
| `split_sources.py` | the raw tables → six systems as `query_manufacturing/` (SQL for Postgres and MySQL, a Mongo dump) and loads all six databases |
| `build_consolidated.py` | the consolidation in Python, from the six sources, checked field for field against the reference: an independent check of the Tapstate pipelines' logic |
| `questions.py`, `questions.json` | the 20 questions and answers computed from the raw tables only; the JSON is the frozen record |
| `answer_check.py`, `make_queries.py` | answer checking (the bank benchmark's checker, unchanged), embedded into each query's `validate.py` |
| `descriptions/` | database descriptions: setup A, setup C |
| `tapstate/`, `run_tapstate.sh` | the Tapstate workspace (six sources, the databases setup A queries; ten pipelines), and the script that applies and starts it in order |
| `verify_tapstate.py` | checks Tapstate's views against the reference; `--install` makes them setup C |
| `reset_dbs.py` | restores setup C and checks the six sources against the dataset files (`--sources` reloads them); run before every batch |
| `dry_run.py` | every database through DAB's own tools, six reference questions per setup, the sandbox and the prompts; no model calls |
| `analyze_runs.py` | pass@1, tokens, turns and cost per correct answer per setup, by category and per question |
| `results/` | the scored runs |
| `tests/` | self-checks, no databases needed |

The commands are in the project's [README](../README.md#reproduce-it).
