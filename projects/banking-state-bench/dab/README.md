# Bank data as a DAB dataset

Builds the DAB datasets for the bank pilot described in [../SOURCE_SPLIT.md](../SOURCE_SPLIT.md):
NPCI tau-indian-banking split into six source systems (setups A and B), the same data
consolidated into one customer document per customer (setup C), and 24 questions with
validators. The agent harness is the agent-benchmark kit's (`harness/` on the `ws/agent-benchmark`
line); nothing here changes it.

| File | What it does |
|---|---|
| `fetch_data.py` | downloads the pinned dataset revision into `data/` and checks file hashes |
| `common.py` | key formats per system and their inverses, and setup C's document shape |
| `split_sources.py` | `db.json` → six systems as `query_bank/` (Postgres SQL, SQLite, DuckDB, Mongo dump) and loads the two server-side ones |
| `build_consolidated.py` | setup C from the six sources (not from `db.json`), checked field for field against upstream |
| `questions.py`, `questions.json` | the 24 questions and answers computed from upstream only; the JSON is the frozen record |
| `answer_check.py`, `make_queries.py` | answer checking, embedded into each query's `validate.py` |
| `descriptions/` | database descriptions: setup A, setup B's hints, setup C |
| `load_bank_to_postgres.py`, `tapstate/` | the six systems in Postgres (`banksrc`) and the Tapstate workspace that consolidates them |
| `verify_tapstate.py` | checks Tapstate's view against upstream; `--install` makes it setup C |
| `dry_run.py` | every database through DAB's own tools, six reference questions per setup, the sandbox and the prompts; no model calls |
| `tests/` | self-checks, no databases needed |

## Build (no model calls)

Needs the Tapstate quickstart stack (Postgres on 55432, MongoDB on 27017) and the benchmark
virtualenv. `<dab>` is the DataAgentBench checkout, `<harness>` the kit's `harness/` directory.

```sh
python fetch_data.py
python split_sources.py <dab>          # query_bank/, Postgres bank_cbs, MongoDB bank_crm
python build_consolidated.py <dab>     # query_bank_consolidated/, MongoDB bank_state
python make_queries.py <dab>           # 24 query directories in both
sg docker -c "python dry_run.py <dab> <harness>"
python -m unittest discover -s tests -v
```

`sg docker` is only needed where the docker group was added after the shell started; DAB's
`execute_python` runs in a container.

## Setup C through Tapstate

```sh
python load_bank_to_postgres.py <dab>  # Postgres banksrc, one schema per system
export TAPSTATE_PASSWORD=...           # the admin password
T="tapstate -c 127.0.0.1:8080 -u admin"
$T apply tapstate                      # then, for each of cbs cards lending payments crm insurance:
$T discover-schema src_bank_<system>   # and apply again, so the nest is sized from row counts
$T start bank_customer_state
python verify_tapstate.py <dab> --install   # checks every document, then makes it setup C
```

`load_bank_to_postgres.py` drops and recreates `banksrc`, which fails while the sources hold
replication slots; stop the pipeline and delete the sources first when rebuilding.

## Pilot

```sh
cd <kit>
sg docker -c "DOCKER_HOST=unix:///var/run/docker.sock python harness/run_claude_arms.py <dab> \
  --dataset bank --model claude-opus-5-5 --arms A,B,C --runs 5 --queries 1-24 --workers 3"
python harness/score.py <dab> --csv bank-pilot.csv
```

The pilot runs on the Claude subscription login (the runner removes API keys from the agent's
environment), so the reported costs are API-equivalent estimates. Runs resume: rerunning the
same command continues rather than repeats. The runner's own default `DOCKER_HOST` is the Docker
Desktop path, so it must be set on Linux.
