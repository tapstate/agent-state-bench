# Bank data as a DAB dataset

Builds the DAB datasets for the bank pilot described in [../SOURCE_SPLIT.md](../SOURCE_SPLIT.md):
NPCI tau-indian-banking split into six source systems (setups A and B), the same data
consolidated into one customer document per customer (setup C), and 24 questions with
validators. The agent harness is this repository's `harness/`.

| File | What it does |
|---|---|
| `fetch_data.py` | downloads the pinned dataset revision into `data/` and checks file hashes |
| `common.py` | key formats per system and their inverses, and setup C's document shape |
| `split_sources.py` | `db.json` → six systems as `query_bank/` (Postgres SQL, SQLite, DuckDB, Mongo dump) and loads the two server-side ones |
| `build_consolidated.py` | the consolidation in Python, from the six sources (not from `db.json`), checked field for field against upstream: an independent check of the Tapstate pipeline's logic |
| `questions.py`, `questions.json` | the 24 questions and answers computed from upstream only; the JSON is the frozen record |
| `answer_check.py`, `make_queries.py` | answer checking, embedded into each query's `validate.py` |
| `descriptions/` | database descriptions: setup A, setup B's hints, setup C |
| `load_bank_to_postgres.py`, `tapstate/` | the six systems in Postgres (`banksrc`) and the Tapstate workspace that consolidates them |
| `verify_tapstate.py` | checks Tapstate's view against upstream; `--install` makes it setup C |
| `reset_dbs.py` | restores every server-side database from the dataset files and checks them; run before every batch |
| `dry_run.py` | every database through DAB's own tools, six reference questions per setup, the sandbox and the prompts; no model calls |
| `tests/` | self-checks, no databases needed |

## Commands

The step-by-step setup, run and scoring commands are in the project's
[README](../README.md#reproduce-it). Setup C comes from Tapstate (`load_bank_to_postgres.py`,
`tapstate/`, `verify_tapstate.py --wait --install`); `build_consolidated.py` builds the same
documents in Python and is kept as an independent check of the consolidation logic.

## Freshness extension

`freshness/` replays 1–6 July into the live core banking database while agents answer, and
compares continuously updated consolidated state (Tapstate) with a nightly warehouse and the live
sources. Design, as-built notes and commands are in [`../FRESHNESS.md`](../FRESHNESS.md) (§13). The
decisions made during the unattended run are in [`freshness/RUN_LOG.md`](freshness/RUN_LOG.md).
The extension points Tapstate's core banking source at `bank_cbs` and stores that database's
dates as text, which also changes what setup A sees if the static pilot is rerun.
