# agent-state-bench

A reproducible benchmark of AI data agents: does an agent answer data questions more
accurately, and more cheaply, when it queries **maintained, entity-resolved state** instead of
stitching the **raw source databases** together itself?

The questions, ground truth and validators come from
[DAB, the Data Agent Benchmark](https://github.com/ucbepic/DataAgentBench) (UC Berkeley and
Hasura PromptQL; paper: [arXiv 2603.20576](https://arxiv.org/abs/2603.20576)). This kit does not
change them. It adds a second way to answer the same questions: one MongoDB database built by
[Tapstate](https://github.com/tapstate/tapstate) CDC pipelines from the same sources, with
entities resolved, values normalized and child records embedded.

Status: work in progress. Results are preliminary until the final report is published here.

## Setups compared

| Setup | Data the agent queries | Guidance |
|---|---|---|
| A | DAB's original databases (Postgres, SQLite, DuckDB, MongoDB) | none |
| B | the same databases | DAB's own hint files |
| C | one MongoDB database built by Tapstate from the same sources | a description of the store's fields and guarantees |

Same agent, tools (DAB's four: query a database, list tables, run Python, return an answer),
prompt and questions in every setup; 5 runs per question. Scores use DAB's validators.
Metrics: pass@1 (per-question averaging, as DAB does), tokens, turns, time, and cost per correct
answer (total spend, failed runs included, divided by correct runs), each with a 95% interval
from a question-clustered bootstrap.

## Layout

| Path | What it holds |
|---|---|
| `harness/` | the runners, an MCP server exposing DAB's tools to Claude Code, the scorer, and `dab.patch` (the changes applied to DAB's harness, listed below) |
| `datasets/` | per dataset: loading the sources into Postgres for CDC, the Tapstate pipelines (generated or in `pipelines/`), entity resolution, and building setup C's dataset directory |
| `datasets/descriptions/` | setup C's database descriptions: fields and guarantees only, no hint text, no per-question guidance |
| `eval/` | grading of entity resolution against published ground truth |
| `stack/` | changes to the Tapstate quickstart compose file |
| `tests/` | self-checks of the kit (`python -m pytest tests`) |

## Setup

1. **Tapstate** v0.5.0 quickstart stack, with `stack/compose.diff` applied: Mongo published on
   `127.0.0.1:27017`, Postgres on `127.0.0.1:55432`, and Postgres `max_wal_senders` /
   `max_replication_slots` raised (one replication slot per pipeline and source; 100 was used).
2. **DAB** at commit `0290945c2b38`: clone it, fetch each dataset's files as DAB describes, then
   `git apply harness/dab.patch`. DAB's repository carries no license file, so this kit ships
   only its own code and the patch, not DAB's code or data. Build the Python sandbox image DAB's
   exec tool uses (`python-data:3.12`, from `python:3.12-slim` with pandas and pyarrow). On
   Docker Desktop set `DOCKER_HOST=unix://$HOME/.docker/run/docker.sock`.
3. **Python 3.12** virtual environment with DAB's requirements plus `mcp` (2.x), `rapidfuzz`,
   `pymongo`, `psycopg2-binary`, `duckdb`.
4. **Sources and pipelines**, per dataset:
   - crmarenapro: `load_crm_to_postgres.py`, `gen_crm_pipelines.py`, start the pipelines,
     `verify_crm_views.py`, `make_consolidated_dataset.py`.
   - cve: `load_cve_to_postgres.py` (uses `cve_decode.py`), `gen_cve_pipelines.py` plus
     `pipelines/cvss_state.tap.yml`, `make_cve_dataset.py`.
   - bookreview and music_brainz_20k: `load_small_to_postgres.py`, `music_er.py`,
     `pipelines/book_state.tap.yml` and `pipelines/music/`, `make_small_datasets.py`.

## Run and score

With a Claude plan login, Claude Code is the agent (`claude -p` per question, only DAB's four
tools, no other tools, MCP servers or settings); cost is Claude Code's API-equivalent estimate:

```sh
python harness/run_claude_arms.py <dab-root> --dataset crmarenapro --model claude-sonnet-5 --arms A,B,C --runs 5
python harness/score.py <dab-root> --csv results.csv
```

With an API key, `harness/run_arms.py` runs DAB's own agent loop instead. Both runners are
resumable. A usage limit pauses a batch and a re-run retries it; an expired login stops the
batch, because waiting cannot fix it.

## Changes to DAB's harness (`harness/dab.patch`)

| Change | Applies to | Why |
|---|---|---|
| Mongo `query_db` accepts an aggregation `pipeline` | all setups | the stock tool is `find()` only, which would handicap any MongoDB-backed setup |
| Mongo `query_db` returns every matching document unless a `limit` is given | all setups | the stock default silently returned 5 documents, unlike SQL, which returns every row |
| `DAB_PERSISTENT_DBS=1`: load a server-side database once and keep it | all setups | the stock harness drops and reloads per run, so parallel runs of one dataset collide |
| `harness/bin/psql`, `harness/bin/mongorestore` run the clients inside containers | all setups | no database clients on the host |

## Known limits

- **cve, setup C** resolves the dataset's scrambled keys with an inverse of DAB's own corruption
  generator: an upper bound on what resolved state can do, not what a product resolver reaches.
- **The cve `cpe_match` collection** is built in Python with the pipeline's exact logic, because
  the Tapstate v0.5.0 join ran out of memory on it (tapstate/tapstate#505); 8 rows a join left
  unmatched are filled from the crosswalk and reported (tapstate/tapstate#496).
- **Some setup C fields were chosen after reading the questions.** Every such choice is checked:
  bookreview without its question-shaped `publication_decade` field scores the same, and the
  music entity resolution is graded against the public MusicBrainz 20K clusters on all songs
  (`eval/eval_music_er.py`: pairwise F1 0.94, 92% of songs exact).
- The data is static; a freshness test, with the sources changing between questions, is in progress.

## License

Apache License 2.0, see `LICENSE`. DAB and its datasets are not included and keep their own terms.
