# Banking state benchmark

Does a data agent answer bank questions better, or more cheaply, when it reads one consolidated
customer record than when it reads the bank's separate source systems? This project measures it
on [DAB](https://github.com/ucbepic/DataAgentBench)'s agent harness, with NPCI's synthetic
[tau-indian-banking](https://huggingface.co/datasets/NPCI/tau-indian-banking) data split into six
source systems, and the consolidated record built by [Tapstate](https://github.com/tapstate/tapstate)
Community.

| Setup | What the agent reads |
|---|---|
| A | six source databases (Postgres, SQLite, DuckDB, MongoDB), each writing customer and account keys in its own format |
| B | the same six databases, with the key formats and join paths described |
| C | one MongoDB collection, one document per customer, built by a Tapstate pipeline from the six sources |

Same agent, tools, prompt and 24 questions in every setup. Design and full results:
[SOURCE_SPLIT.md](SOURCE_SPLIT.md). The freshness extension (live sources, a nightly warehouse,
and continuously updated state): [FRESHNESS.md](FRESHNESS.md). The original research plan:
[CHARTER.md](CHARTER.md).

## Results: Claude Opus 5.5

Two batches of 360 runs (24 questions × 3 setups × 5 runs), on 2026-10-09 and 2026-10-30, pooled:

| Setup | pass@1 (95% CI) | Median tokens per run | Median turns |
|---|---|---:|---:|
| A: six sources, no hints | 0.992 (0.98–1.00) | 77,184 | 7 |
| B: six sources, key hints | 0.983 (0.96–1.00) | 73,956 | 7 |
| C: consolidated state | 0.992 (0.98–1.00) | 22,184 | 4 |

The agent was equally accurate in every setup, and used about 3.5 times fewer tokens on the
consolidated state. Every failure in both batches was a protocol miss (a correct answer never
submitted). Per batch: [dab/PILOT_RESULTS_opus.md](dab/PILOT_RESULTS_opus.md) and
[dab/RERUN_RESULTS_opus.md](dab/RERUN_RESULTS_opus.md).

## Reproduce it

Tested from scratch on Linux (Ubuntu 26.04, Docker Engine 29, Compose 2.40). macOS with Docker
Desktop should work with the `DOCKER_HOST` change in step 1 and `shasum -a 256` in place of
`sha256sum`, but has not been tested. Needs Docker with Compose v2, Python 3.12, git, about 8 GB
of disk, and for the agent runs either a Claude login for Claude Code or an API key for an
OpenAI-compatible endpoint. All commands run from the root of this repository.

### 1. Settings

```sh
. projects/banking-state-bench/env.sh
```

Source it in every shell you use. It sets the ports (Postgres 55432, MongoDB 27017, Tapstate
8080), the database credentials, and what the harness needs. If a port is taken, export a
different one first (`export PG_PORT=56432` and so on), then source it. On Docker Desktop, also
`export DOCKER_HOST=unix://$HOME/.docker/run/docker.sock` first.

### 2. Stack: Tapstate Community 0.7.0, MongoDB, Postgres

```sh
docker compose -p "$BANK_STACK" -f projects/banking-state-bench/stack/docker-compose.yml up -d
docker compose -p "$BANK_STACK" -f projects/banking-state-bench/stack/docker-compose.yml wait bootstrap  # exits 0 once the admin exists
docker build -t python-data:3.12 -f stack/python-data.Dockerfile stack   # the agent's Python sandbox
```

Install the Tapstate CLI from the
[v0.7.0 release](https://github.com/tapstate/tapstate/releases/tag/v0.7.0) (Linux x64 shown;
releases for arm64 and macOS sit beside it), then register the two connectors the pipeline uses:

```sh
curl -sSLO https://github.com/tapstate/tapstate/releases/download/v0.7.0/tapstate-0.7.0-linux-x64.tar.gz
curl -sSLO https://github.com/tapstate/tapstate/releases/download/v0.7.0/tapstate-0.7.0-linux-x64.tar.gz.sha256
sha256sum -c tapstate-0.7.0-linux-x64.tar.gz.sha256 && tar xzf tapstate-0.7.0-linux-x64.tar.gz
T="$PWD/tapstate-cli-0.7.0/bin/tapstate -c $TAPSTATE_SERVER -u admin"
$T register postgres
$T register mongodb
```

### 3. DAB and Python

DAB is not redistributed here (its repository has no license file), so clone it at the pinned
commit and apply this kit's changes to its harness. The bank benchmark needs none of DAB's own
datasets, so their large files can be skipped.

```sh
GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/ucbepic/DataAgentBench.git ../DataAgentBench
git -C ../DataAgentBench checkout 0290945c2b38
git -C ../DataAgentBench apply "$PWD/harness/dab.patch"
DAB="$(cd ../DataAgentBench && pwd)"

python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
```

The patch's changes are listed in the kit's [README](../../README.md#changes-to-dabs-harness-harnessdabpatch).

### 4. Data

```sh
cd projects/banking-state-bench/dab
python fetch_data.py                 # the pinned NPCI revision, with file hashes checked
python split_sources.py "$DAB"       # setups A/B: the six source systems, loaded into Postgres and MongoDB
python make_queries.py "$DAB"        # the 24 questions, answers and validators, for all setups
```

### 5. Setup C through Tapstate

```sh
python load_bank_to_postgres.py "$DAB"         # the six systems as Tapstate's sources (Postgres banksrc)
$T apply "$PWD/tapstate"                            # sources and the bank_customer_state pipeline
for s in cbs cards lending payments crm insurance; do $T discover-schema src_bank_$s; done
$T apply "$PWD/tapstate"                            # again, so the pipeline is sized from row counts
$T start bank_customer_state
python verify_tapstate.py "$DAB" --wait --install
```

The last step waits until the pipeline's view holds all 944 customers, checks every document
field for field against the upstream record, and only then installs it as setup C. It takes a
few minutes.

### 6. Check, then run

```sh
python reset_dbs.py "$DAB"           # restore every database from the dataset files; before every batch
python dry_run.py "$DAB" ../../../harness    # no model calls: databases, reference answers, sandbox, prompts
cd ../../..
```

Run a smoke test first (`--runs 1 --queries 1`: three runs), then the full batch. Runs resume:
rerunning a command continues it, and a new batch on the same checkout needs `--runs` raised
(runs 5 to 9 are made by `--runs 10`).

**Claude through Claude Code**, as published. It uses the Claude login of the machine and
removes API keys from the agent's environment:

```sh
python harness/run_claude_arms.py "$DAB" --dataset bank --model claude-opus-5-5 \
  --arms A,B,C --runs 5 --queries 1-24 --workers 3
```

**Any other model**, through an OpenAI-compatible endpoint (a provider's API, OpenRouter,
Together, a local vLLM server), with DAB's own agent loop:

```sh
export DAB_LLM_BASE_URL=https://openrouter.ai/api/v1 DAB_LLM_API_KEY=...
python harness/run_arms.py "$DAB" --dataset bank --model moonshotai/kimi-k2 \
  --arms A,B,C --runs 5 --queries 1-24 --workers 3
```

The two runners give the agent the same tools, data and questions, but differ in the agent loop
and in the tool-call instructions in the system prompt. Compare setups within one runner and
model; do not compare token counts across runners.

### 7. Score

```sh
python harness/score.py "$DAB" --csv scores.csv
python projects/banking-state-bench/dab/analyze_pilot.py scores.csv harness --model cc-claude-opus-5-5
```

`--model` is the model as it appears in the run directories: `cc-<model>` for Claude Code runs,
and the model name with `/` replaced by `-` for the other runner. For a model the scorer has no
price for, add `--price <model>=<input>,<output>,<cached input>` in USD per million tokens to
`score.py`.

### What to expect

The 360-run Opus 5.5 batch took 42 minutes with three workers, used about 20.5 million tokens,
and cost $42 at API list prices (Claude Code's API-equivalent estimate). Agent runs are
nondeterministic: expect the pass rates within the confidence intervals above and the median
token ratio between setups A/B and C near 3.5, not identical token counts.

### Troubleshooting

- **`permission denied` on the Docker socket:** add yourself to the `docker` group and log in
  again, or prefix the runner commands with `sg docker -c "..."`.
- **`reset_dbs.py` refuses because of a replication slot:** the freshness extension's pipeline
  still reads `bank_cbs`. Stop it (`$T stop bank_customer_state`) and drop the slot it names.
- **The freshness extension** ([FRESHNESS.md](FRESHNESS.md) §13) changes `bank_cbs`; run
  `reset_dbs.py` before going back to the source-split batch.
