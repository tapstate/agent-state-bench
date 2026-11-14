# Manufacturing state benchmark

Does a data agent answer manufacturing questions better, or more cheaply, when it reads
consolidated state than when it reads a manufacturer's separate source systems? This project
measures it on [DAB](https://github.com/ucbepic/DataAgentBench)'s agent harness, with Microsoft's
[AdventureWorks](https://github.com/microsoft/sql-server-samples) sample (MIT) split into six source
systems, and the consolidated state built by [Tapstate](https://github.com/tapstate/tapstate)
Community. It mirrors the [bank benchmark](../banking-state-bench/README.md).

| Setup | What the agent reads |
|---|---|
| A | six source databases on three engines (ERP, PLM and costing on Postgres; MES and procurement on MySQL; WMS on MongoDB), each writing the product number in its own format |
| C | one MongoDB database with four collections (products, work orders, sales orders, purchase orders), built by Tapstate pipelines from the six sources |

Same agent, tools, prompt and 20 questions in both setups. Design and full results:
[SOURCE_SPLIT.md](SOURCE_SPLIT.md).

## Results: Claude Opus 5.5

200 runs (20 questions × 2 setups × 5 runs) on 2026-11-12:

| Setup | pass@1 (95% CI) | Median tokens per run | Mean tokens per run | Median turns | API-equivalent $ per correct answer |
|---|---|---:|---:|---:|---:|
| A: six source systems | 0.980 (0.95–1.00) | 63,818 | 74,800 | 6 | 0.150 |
| C: consolidated state | 0.980 (0.95–1.00) | 28,432 | 34,515 | 5 | 0.067 |

The agent was equally accurate in both setups and used about 2.2 times fewer tokens on the
consolidated state; the gap is absent on single-source controls (1.1×), 2.3× on two- and
three-or-more-source questions, and 3.2× on portfolio aggregates. All four failures were protocol
misses (a correct answer never submitted). Per category and per question:
[dab/RESULTS_opus.md](dab/RESULTS_opus.md). The gap is smaller than the bank benchmark's 3.5×;
[SOURCE_SPLIT.md](SOURCE_SPLIT.md) §9 discusses why, and what a finer split would do.

**Layout.** This batch ran on the first layout: PLM in SQLite, MES and procurement in DuckDB, ERP and costing in Postgres, WMS in MongoDB, with Tapstate reading a Postgres copy of the six systems. The code now builds the three-engine layout (Postgres, MySQL, MongoDB; Tapstate reads the six databases directly; [SOURCE_SPLIT.md](SOURCE_SPLIT.md) §2–3). The 200-run batch has not been repeated on it yet; its smoke test and the sales-agent demo runs are in [demo/results/RESULTS.md](demo/results/RESULTS.md).

## Reproduce it

Tested on Linux (Ubuntu 26.04, Docker Engine 29, Compose 2.40). Needs Docker with Compose v2,
Python 3.12, git, about 8 GB of disk, and for the agent runs a Claude login for Claude Code. All
commands run from the root of this repository. The stack uses the bank benchmark's ports, so stop
that stack first if it is running.

### 1. Settings

```sh
. projects/manufacturing-state-bench/env.sh
```

Source it in every shell you use. It sets the ports (Postgres 55432, MySQL 33306, the WMS MongoDB
27018, Tapstate's MongoDB 27017, Tapstate 8080), the database credentials, and what the harness needs. Override a port by exporting it
before sourcing (`export PG_PORT=56432`).

### 2. Stack: Tapstate Community 0.7.0, Postgres, MySQL, MongoDB

```sh
docker compose -p "$MFG_STACK" -f projects/manufacturing-state-bench/stack/docker-compose.yml up -d
docker compose -p "$MFG_STACK" -f projects/manufacturing-state-bench/stack/docker-compose.yml wait bootstrap
docker build -t python-data:3.12 -f stack/python-data.Dockerfile stack   # the agent's Python sandbox
```

Install the Tapstate CLI from the
[v0.7.0 release](https://github.com/tapstate/tapstate/releases/tag/v0.7.0) and register the two
connectors (MySQL's is not bundled with the server; `register` fetches the published one):

```sh
curl -sSLO https://github.com/tapstate/tapstate/releases/download/v0.7.0/tapstate-0.7.0-linux-x64.tar.gz
curl -sSLO https://github.com/tapstate/tapstate/releases/download/v0.7.0/tapstate-0.7.0-linux-x64.tar.gz.sha256
sha256sum -c tapstate-0.7.0-linux-x64.tar.gz.sha256 && tar xzf tapstate-0.7.0-linux-x64.tar.gz
export TAPSTATE_CLI="$PWD/tapstate-cli-0.7.0/bin/tapstate"
T="$TAPSTATE_CLI -c $TAPSTATE_SERVER -u admin"
$T register postgres
$T register mongodb
$T register mysql
```

### 3. DAB and Python

As in the bank benchmark: clone DAB at the pinned commit and apply this kit's patch (it also
lists `manufacturing` and `manufacturing_consolidated` as datasets). None of DAB's own datasets
is needed.

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/ucbepic/DataAgentBench.git ../DataAgentBench
git -C ../DataAgentBench checkout 0290945c2b38
git -C ../DataAgentBench apply "$PWD/harness/dab.patch"
DAB="$(cd ../DataAgentBench && pwd)"

python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

### 4. Data

```sh
cd projects/manufacturing-state-bench/dab
python fetch_data.py                 # the AdventureWorks OLTP install script, sha256 checked, into data/raw
python split_sources.py "$DAB"       # setup A: the six source systems, loaded into Postgres, MySQL and MongoDB
python make_queries.py "$DAB"        # the 20 questions, answers and validators, for both setups
```

### 5. Setup C through Tapstate

```sh
sh run_tapstate.sh                        # Tapstate reads the six databases; applies, starts the ten pipelines in order
python verify_tapstate.py "$DAB" --wait --install
```

`run_tapstate.sh` stops and clears any earlier run of the ten pipelines, applies the sources (the six
databases setup A queries, read through logical replication, the MySQL binlog and change streams), discovers them, and starts the pipelines in dependency
order: the product reference and staging pipelines first, then (once their views are complete) the
four that build setup C ([SOURCE_SPLIT.md](SOURCE_SPLIT.md) §3 explains the layout). The last step
waits until the four views hold every document and have stopped changing, checks every document
field for field against a reference built from the raw AdventureWorks tables, and only then
installs them as setup C (MongoDB `mfg_state`). The whole build took about half an hour on an
8-core machine. `python build_consolidated.py "$DAB"` builds the same documents in Python from the
six sources and checks them the same way; with `--install` it can stand in for Tapstate's output.

### 6. Check, then run

```sh
python reset_dbs.py "$DAB"                    # restore setup C, check the sources' row counts; before every batch
python dry_run.py "$DAB" ../../../harness     # no model calls: databases, reference answers, sandbox, prompts
cd ../../..
python harness/run_claude_arms.py "$DAB" --dataset manufacturing --model claude-opus-5-5 \
  --arms A,C --runs 5 --queries 1-20 --workers 3
```

Run a smoke test first (`--runs 1 --queries 14`: two runs). Runs resume: rerunning continues a
batch, and a new batch on the same checkout needs `--runs` raised. `harness/run_arms.py` runs DAB's
own agent loop for any OpenAI-compatible endpoint instead (see the bank README); compare setups
within one runner and model.

### 7. Score

```sh
python harness/score.py "$DAB" --csv scores.csv
python projects/manufacturing-state-bench/dab/analyze_runs.py scores.csv harness --model cc-claude-opus-5-5
```

### Troubleshooting

- **Reloading the sources** (`split_sources.py`, or `reset_dbs.py --sources`) drops the databases
  Tapstate captures from: stop the pipelines first, then rerun `run_tapstate.sh`. A Postgres
  database with an active replication slot cannot be dropped; `split_sources.py` drops inactive
  slots itself.
- **A view stays short of its document count:** read `$T status <pipeline>`; a failed pipeline
  prints its reason. A pipeline started before the views it reads were discovered must be
  stopped, the workspace applied again, and the pipeline restarted.
