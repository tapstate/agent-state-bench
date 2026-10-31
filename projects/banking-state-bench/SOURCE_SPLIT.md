# Bank dataset as a DAB dataset: source-split design

Status, 2026-10-09: built (code in [`dab/`](dab/README.md)). Setup C is Tapstate's output,
verified field for field against upstream (§3). Pilot with Claude Opus 5.5 complete; results are
in §9. §8 lists where the build differs from the first draft.

Goal: answer the charter's question with DAB's harness. Does an agent answer bank data questions
more accurately and with fewer tokens when it queries one consolidated store (setup C) instead
of the bank's separate source systems (setups A and B)? The dataset is NPCI TauIndianBankBench
(MIT, synthetic), split into source systems that a real bank runs separately.

This is a **read-only question-answering** use of the bank data, not the upstream tau2
conversation benchmark. Scores here are DAB-style pass@1 on questions we write, not
TauIndianBankBench scores.

## 1. Data facts that constrain the design

Profiled from `NPCI/tau-indian-banking` revision `747d97ff1e51f0f9a97374a5603d27bc438984a4`
(Hugging Face, last modified 2026-09-08; reachable on 2026-10-09, unlike on 2026-10-04 per
FINDINGS.md). `db.json`: 8.6 MB, one nested record per customer.

| Entity | Rows | Link to owner in the source data | ID globally unique |
|---|---:|---|---|
| customers | 944 | `CUST_xxxxxxxx` | yes |
| accounts (944 savings, 362 current) | 1,306 | nested under customer; 711 list a second holder **by name only** | yes |
| transactions | 7,745 (at most 8 per account) | nested under account | yes |
| cards (944 debit, 447 credit) | 1,391 | `linked_account` (always the owner's account) | **no**: 14 IDs shared by 29 different cards |
| deposits (823 FD, 531 RD) | 1,354 | `payout_account` (always the owner's) | **no**: 1 ID shared by 2 deposits |
| loans (home, personal, gold) | 445 | **only by nesting**; no account or customer field | **no**: 1 ID shared by 2 loans |
| insurance policies | 334 | **only by nesting** | yes |
| mandates | 1,545 | `account_id` (always the owner's) | yes |
| service requests | 957 | `account_id` (always the owner's) | yes |
| offers | 1,634 | nested; a catalog of 1,523 distinct offers | no (shared catalog) |

Two consequences:

- **Customer attributes cannot join the sources.** 555 customers share the phone number
  `9876543210`, 58 are named "Rajesh Kumar", and 68 email addresses repeat. Every source must
  carry a real key (customer or account). The joins test key handling, not fuzzy matching.
- **Card, deposit and loan IDs need composite keys.** Upstream gets away with collisions
  because every tool call is scoped to the logged-in customer. In split sources the key is
  `(card_id, account)`, `(deposit_id, account)` and `(loan_id, customer)`. Questions must not
  name a colliding ID without its owner.

The domain runs on a frozen clock, 2026-07-16. Questions use explicit dates, never "last month".

## 2. Source systems

Six systems, laid out the way Indian retail banks commonly separate them. Engines follow DAB's
mix (Postgres, SQLite, DuckDB, MongoDB) so setup A looks like DAB's own datasets.

| System | Engine (setups A/B) | Tables / collections | Owner key, as that system writes it |
|---|---|---|---|
| **Core banking (CBS)** | Postgres | `customers` (name, age, gender, senior_citizen, kyc_status, monthly_income, addresses), `accounts`, `account_holders`, `transactions`, `deposits` | `customer_id` = `CUST_35359B3E` (canonical); `account_id` = `SB1785318658` |
| **Cards (CMS)** | SQLite | `cards`, `card_controls` (one row per channel × region) | no customer key; `account_no` = `1785318658` as an **integer** (type prefix dropped) |
| **Lending (LMS)** | DuckDB | `loans` | `borrower_ref` = `35359b3e` (prefix dropped, lower case) |
| **Payments hub** | SQLite | `mandates` | `debit_account` = `OURB0105821/SB1785318658` (IFSC-qualified) |
| **CRM** | MongoDB | `customers` (segment, profession, sector, employer, marital_status, email, phone), `service_requests`, `offers` | `_id` = `CUST-35359B3E` (hyphen); requests carry the canonical `account_id` |
| **Bancassurance feed** | DuckDB | `policies` (partner insurer's extract) | `policyholder_ref` = `OURB:35359B3E` |

Each key transform is deterministic and reversible, and was checked for collisions: numeric
account suffixes are unique, and customer suffixes are unique when lower-cased. Every row in the
source data reaches its owner through some chain of keys:

```
CRM._id ─┐                    ┌─ CMS.account_no (int) ── cards, card_controls
LMS.borrower_ref ─┼─ CBS.customer_id ── CBS.account_id ─┼─ Payments.debit_account ── mandates
Insurance.policyholder_ref ─┘     │                     └─ CRM.service_requests.account_id
                                  └─ CBS.deposits, transactions
```

Cards and mandates reach a customer only through an account, and loans and policies only
through a customer key. That makes many questions multi-hop by construction.

**Deliberately not done in this phase:**

- No conflicting values between systems, such as a different address in CRM and CBS. That
  would need a resolution rule and changed ground truth. It is a candidate later stressor.
- No probabilistic entity resolution. With the duplicate names and phones above, the result
  would be noise rather than a test.
- No freshness or change-capture timing. That is the charter's dynamic phase, which can use
  the upstream write tools to change sources.

## 3. Setups

| Setup | What the agent queries | Guidance file |
|---|---|---|
| A | the six source databases | `db_description.txt`: tables and columns per system, no key hints |
| B | the same six databases | `db_description_withhint.txt`: A plus the key formats and join paths in §2 |
| C | one MongoDB `customers` collection, one document per customer | fields and guarantees only |

**Setup C's shape is the upstream customer record, not something designed for the questions.**
The only change is turning ID-keyed maps (`accounts: {SB…: {…}}`) into arrays of objects, which
MongoDB queries need. This avoids the known limit in the main README, where some setup C fields
were chosen after reading the questions.

**Building setup C:** the Tapstate pipeline `dab/tapstate/pipeline/bank_customer_state.tap.yml`
reads Postgres copies of all six sources (database `banksrc`, one schema per system). It has one
`js` step per table that maps that system's key back to the canonical form. Rows that carry only
an account (deposits, cards, mandates, service requests) are nested under that account, and a final
step lifts them to the customer. Tapstate joins can read only source tables, not earlier steps, so
the pipeline uses no joins.

- **Integer card account numbers.** Tapstate's nest matches keys by typed value
  (`NestKeys.java`, see the bank c360 feasibility note), so an integer never equals a string.
  The pipeline therefore compares string keys on both sides.
- **Sibling embeds share one parent key.** Tapstate requires every embed under one parent to
  join on the same parent field. Everything under an account therefore joins on the account
  number without its prefix, the cards system's only account key, which is unique.
- **DATE values are unreadable in `js` steps (Tapstate 0.6.0).** A Postgres DATE reaches a `js`
  step as an opaque object: no fields, `JSON.stringify` gives `{}`, and its text form is
  "[object Object]". The Tapstate source copy (`banksrc`) therefore stores those six columns as
  ISO text; the values are unchanged, and the agents' databases keep their DATE types. This is
  worth reporting upstream.
- **Status:** applied on a 0.6.0 development build (v0.6.0-174-g9af4288fc, included in release
  0.7.0) and run to a complete snapshot. All 944 documents equal the upstream records field for
  field (`dab/verify_tapstate.py`). That output is installed as setup C (MongoDB `bank_state`).
  `dab/build_consolidated.py`, the Python build of the same mappings, agrees with it and remains
  as a cross-check.
- **Release 0.7.0 (2026-10-30):** the same workspace, applied from scratch on Tapstate Community
  0.7.0 (the published server image and CLI, connectors registered from the release), gives
  944 of 944 documents equal to upstream, and a setup C dump document-for-document identical to
  the published one. The reproduction guide ([README.md](README.md)) pins 0.7.0.

**Acceptance check for setup C:** every document built from the six sources equals the upstream
`db.json` record after the map-to-array change, field for field. The Python build passes: 944 of
944 customers. A test confirms the check fails when one source value or key is changed. Ground
truth for setup C is therefore exact.

## 4. Questions

A pilot of **24 questions**, written before any run and frozen in `dab/questions.json`; a test
fails if the questions or their answers change. The full list with answers is in that file.

| Category | Count | Example (question number) |
|---|---:|---|
| Single-source controls (consolidation should not help) | 4 | How many recurring deposits (RD) have a maturity date between 2026-10-01 and 2026-12-31, inclusive? (1) |
| Two sources | 8 | Customer `CUST_…` has frozen cards. List each frozen card's id and the id of the bank account it is linked to. (5) |
| Three or more sources | 8 | How many customers in the HNI segment hold a home loan and have at least one paused mandate? (13) |
| Portfolio aggregates across sources | 4 | For each customer segment, what is the total outstanding amount across all loans? (21) |

Rules:

- Customer-scoped questions name the customer by the canonical `customer_id`, since names are
  ambiguous. The customer is the first by id that meets a stated rule (`dab/questions.py`) and
  owns no colliding card, deposit or loan id.
- Topics come from the upstream task families (balances, card status, deposit maturity, EMI,
  mandates, service requests), so the questions reflect what bank staff actually look up. Insurer
  names and offer types are avoided: they are free text with many spellings of the same thing, so
  questions on them would test string cleanup, not consolidation.
- `dab/questions.py` computes every answer from upstream `db.json`, independently of all three
  setups. No answer is zero, and no "which one is largest" question has a tie.
- Validators use DAB's format (`query.json`, `ground_truth.csv`, `validate.py`) and are identical
  across setups. Each embeds `dab/answer_check.py`: counts must appear as standalone numbers,
  amounts within ₹1 (₹0.01 for averages, ₹0.50 for a whole-rupee total), ids exactly, with known
  wrong ids rejected; multi-part answers are checked part by part. Every validator accepts its
  reference answer and rejects a wrong one.

## 5. Pilot run (needs your approval and a spend cap)

- Agent: Claude Opus 5.5 (`claude-opus-5-5`) through `harness/run_claude_arms.py`, on the Claude
  subscription login, so there is no metered spend. Reported costs are Claude Code's
  API-equivalent estimates, used to compare setups, not money spent.
- Volume: 24 questions × 3 setups × 5 runs = 360 runs.
- Procedure: a 3-run smoke test (question 1, one run per setup) answered correctly in all three
  setups before the full batch. A subscription usage limit marks runs as not run, and a rerun
  resumes them. The commands are in `dab/README.md`.
- Report: pass@1, input/output tokens, turns, time, and cost per correct answer, with
  question-clustered bootstrap intervals. Single-source controls are reported separately.

## 6. What was built

All under `dab/`; `dab/README.md` has the commands.

1. `fetch_data.py`: the pinned revision, with file hashes checked.
2. `split_sources.py`: `db.json` → the six systems in §2, as setups A/B's DAB dataset
   (`query_bank`), loading Postgres `bank_cbs` and MongoDB `bank_crm`.
3. `build_consolidated.py`: setup C (`query_bank_consolidated`, MongoDB `bank_state`), built from
   the six sources and checked against upstream.
4. `questions.py`, `answer_check.py`, `make_queries.py`: the 24 query directories in both datasets.
5. `descriptions/`: setup A's description, setup B's hints, setup C's description.
6. `load_bank_to_postgres.py`, `tapstate/`, `verify_tapstate.py`: the Tapstate route to setup C (§3).
7. `dry_run.py`: model-free check through DAB's own tools. It passes:
   - all seven databases load and list;
   - six reference questions answered with real queries pass their validators in setups A/B
     (joining on each system's key format) and in setup C;
   - the `execute_python` sandbox runs;
   - only setup B's prompt carries the hints.
8. `tests/`: 13 self-checks, no databases needed.

## 7. Disclosures for any write-up

- The source split and key formats are our construction. They follow common bank system
  boundaries and DAB's ill-formatted-key pattern, but no bank's actual schema.
- The questions are ours, not NPCI's. The data and its licence are NPCI's.
- Joins are deterministic key translations. Setup B's hints reveal all of them, so B versus C
  isolates the cost of doing the joins from the cost of discovering them.

## 8. Changes from the first draft

- **Every card is linked to a savings account** (944 debit, 447 credit), so the draft's
  card/account-type example could not be asked. Question 5 instead asks for each frozen card's
  linked account id, which setups A/B must translate from the cards system's integer.
- **Two draft questions had zero answers** (every service request is on a savings account, and a
  credit-limit threshold matched nobody). They were replaced (questions 9 and 24) before
  freezing.
- **Several status fields never vary:** every account, deposit and loan is ACTIVE, and every
  service request is OPEN with category general_query. Questions use fields that do vary.
- **`login_context` is kept in setup C:** it is part of the upstream record. It is derivable
  from the embedded arrays.
- **DAB's query tool returns every value as a string,** on every engine. This affects all
  setups alike.
- **Tapstate pipeline:** no joins, and string nest keys (§3).
- **Code location:** `dab/` in this project rather than `datasets/bank/` in the benchmark kit,
  so that this branch owns only banking-project files, as the charter requires.

## 9. Pilot results: Claude Opus 5.5, 2026-10-09

360 of 360 runs completed: 24 questions × 3 setups × 5 runs, on the subscription, with no usage
limits hit. Full tables, by category and per question, are in
[`dab/PILOT_RESULTS_opus.md`](dab/PILOT_RESULTS_opus.md); raw scores are in
[`dab/results/opus_scores.csv`](dab/results/opus_scores.csv).

| Setup | pass@1 (95% CI) | Median tokens per run | Mean tokens per run | Median turns | API-equivalent $ per correct answer |
|---|---|---:|---:|---:|---:|
| A: six sources, no hints | 1.000 (1.00–1.00) | 79,358 | 74,051 | 7 | 0.172 |
| B: six sources, key hints | 0.992 (0.97–1.00) | 74,508 | 70,505 | 7 | 0.162 |
| C: consolidated state | 0.992 (0.97–1.00) | 22,074 | 26,112 | 4 | 0.057 |

- **Tokens:** setup C used about 3.5× fewer tokens than A or B at the median (2.8× at the mean).
  The effect appears on every multi-source category: 3.4–3.6× on two-source and
  three-or-more-source questions, and 2.2–2.3× on portfolio totals. It is absent on the
  single-source controls (1.0–1.1×), as designed. Setup C also needed about half the turns, and
  its API-equivalent cost per correct answer was about one third of A's or B's.
- **Accuracy:** no difference. Opus 5.5 is at the ceiling in every setup. There are two failures
  in 360 runs: question 14 in B and question 7 in C. In both, the agent wrote a correct answer
  but never called `return_answer`, which counts as no answer under DAB's protocol. They do not
  separate the setups. This pilot therefore
  does not support an accuracy claim for this model. A weaker model, or harder questions such as
  conflicting values between systems (§2), would be needed to test one.
- **Hints (B versus A):** no measurable effect on accuracy, and only about 5% fewer tokens. Opus
  discovered the key formats from the data by itself.

**Validator correction, disclosed.** The first scoring failed 26 correct answers because of
three validator defects:
1. ID lists rejected answers that named a non-qualifying ID in order to exclude it.
2. Labeled values had to be the first number after the label, which fails table rows.
3. Defect 1 hit setups A and B harder: their agents name the customer's other account while
   checking it, so the defect biased the comparison toward C.

The validators were corrected (`dab/answer_check.py`, version 2), identically for all setups, and
the 360 recorded answers were rescored. The only verdicts that changed are the 26 diagnosed
ones, all from fail to pass; none changed from pass to fail. Before the correction, pass@1 was
A 0.917, B 0.900 and C 0.950 ([`dab/results/opus_scores_validator_v1.csv`](dab/results/opus_scores_validator_v1.csv)). The token figures are
unaffected.

## 10. Rerun: Claude Opus 5.5, 2026-10-30

To check that the pilot's result is stable, the same 360 runs were made a second time: 24
questions × 3 setups × 5 runs, recorded as runs 5 to 9 so the pilot's runs 0 to 4 are untouched.
The harness, model, prompts, data and validators were the pilot's: all 72 question and setup
pairs got a system prompt byte-identical to the pilot's, every database was first restored from
the frozen dataset files (`dab/reset_dbs.py`), and the model-free dry run passed. 360 of 360 runs
completed, with no usage limits hit. Full tables, for the rerun alone and pooled with the pilot,
are in [`dab/RERUN_RESULTS_opus.md`](dab/RERUN_RESULTS_opus.md); raw scores are in
[`dab/results/opus_scores_rerun.csv`](dab/results/opus_scores_rerun.csv).

| Setup | pass@1, pilot | pass@1, rerun | pass@1, pooled (95% CI) | Median tokens, pilot | Median tokens, rerun | Median tokens, pooled |
|---|---:|---:|---|---:|---:|---:|
| A: six sources, no hints | 1.000 | 0.983 | 0.992 (0.98–1.00) | 79,358 | 74,418 | 77,184 |
| B: six sources, key hints | 0.992 | 0.975 | 0.983 (0.96–1.00) | 74,508 | 73,876 | 73,956 |
| C: consolidated state | 0.992 | 0.992 | 0.992 (0.98–1.00) | 22,074 | 22,552 | 22,184 |

- **The token result reproduced.** Setups A and B used 3.3× the median tokens of setup C in the
  rerun (3.4–3.6× in the pilot; 3.5× pooled), with about 7 turns against 4. By category: 3.2–4.2×
  on two-source and three-or-more-source questions, 2.6–3.0× on portfolio totals, and none on the
  single-source controls, as in the pilot. API-equivalent cost per correct answer was $0.046 in
  setup C against $0.150–0.165 in A and B.
- **No wrong answers in either batch.** The rerun's six failures (A on questions 18 and 22, B on
  questions 7, 14 and 23, C on question 8) are all protocol misses: the agent's final text has the
  correct answer, but it never called `return_answer`. The pilot had two of the same kind. Six in
  360 against two in 360 is too few to read anything into; the Claude Code release driving the
  agent (2.1.314 for the rerun) may also differ from the pilot's, which was not recorded.
- **Database state matters.** Before this rerun, Postgres `bank_cbs` still held the freshness
  extension's version of core banking (dates as text, replayed July transactions), so it no longer
  matched the frozen `cbs.sql`. It was restored before the batch. `dab/reset_dbs.py` now does this
  for every server-side database and checks the result; run it before every batch.
