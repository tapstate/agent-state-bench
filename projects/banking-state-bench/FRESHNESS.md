# Freshness extension: continuously updated state vs a nightly warehouse

Status: complete. Both question sets were run (3 passes each, Opus 5.5, 432 episodes): the core set's results are in §14, the cross-system set's in §15, and the summary in §16. Written 2026-10-09 on top of the bank DAB benchmark in
[`SOURCE_SPLIT.md`](SOURCE_SPLIT.md), which tested consolidation with every source static. This
extension adds time: one source keeps changing while agents answer, and the consolidated state is
either continuously updated (Tapstate change capture) or rebuilt once a day (a nightly warehouse).
It fills the freshness axis of the 2×2 in [`README.md`](README.md) for read-only questions.

## 1. Claim under test

> Where agents may not query the live systems, continuously updated consolidated state lets them
> answer questions about current business state correctly and cheaply; a nightly warehouse cannot
> answer them for anything that changed since the last load.

**Deployment assumption.** Enterprises commonly give agents a warehouse that already exists and
keep them off the operational systems, for security and to protect those systems' performance.
Both a nightly warehouse and continuously updated state meet that rule: neither sends agent
queries to the live systems. So the realistic comparison is between those two. Live access is
included only as a reference arm (§3), not as a deployment option.

That the warehouse is wrong about changed records is true by construction and is not the finding.
What the experiment measures:

1. Whether continuously updated state is actually current when an agent reads it: the delay from a
   source commit to the change being readable, measured, not assumed.
2. Whether agents reading it are as accurate as agents reading the live sources, and at what token
   cost. The static pilot found about 3.5× fewer tokens.
3. What agents with only a warehouse do about staleness: whether they warn that the answer reflects
   last night's load, or state a stale value as current. That is a risk in its own right.

## 2. What the data supports (measured on the pinned dataset)

- **Balances and transactions can be replayed exactly.**
  - Every account has a dated transaction history (7,745 transactions, 2026-06-14 to 2026-07-06),
    stored newest first, each with `balance_after`.
  - The ledger is fully consistent: each account's newest `balance_after` equals its
    `available_balance` (1,306 of 1,306), and every step adds up (6,439 of 6,439).
  - So the core banking system (CBS) can be rewound to any past date and replayed forward, ending
    exactly at the published dataset.
- **Daily volume:** 307–624 accounts get a transaction on each day from 1 to 5 July.
- **Every account's newest transaction is dated 2026-07-06** (1,306 of 1,306; exactly one each), a
  generator artifact. That day is a burst in which every account changes, so it gives no unchanged
  controls; it is replayed but not asked about.
- **Transactions have a date but no time of day.** Times within a day have to be generated (§4).
- **Nothing else has history.** Cards (342 frozen), service requests (957 open), mandates, loans,
  policies, deposits and offers are snapshots. Changing them means generating events, which is
  phase 2 (§11).

## 3. Arms

| Arm | The agent reads | Freshness | Relation to the static pilot |
|---|---|---|---|
| **D** (direct source, reference only) | the six live source systems | current | setup A |
| **U/C** (continuous) | Tapstate's consolidated documents | change capture | setup C |
| **U/P** (nightly warehouse) | the same documents, reloaded once a day at 00:00 | lagged | new |

- **D is a reference, not a deployment option.** It breaks the no-live-access rule of §1. It gives
  the best achievable accuracy and the cost of reading the sources directly, which shows whether
  U/C matches the live systems. The README requires a direct-source control.

- U/P and U/C serve **byte-identical documents** at the moment of each nightly load. Each load copies
  Tapstate's view (`views.bank_customer`) into the warehouse database, so the only difference
  between the arms is when the data was last copied.
- **Both consolidated arms are told the truth about freshness** in their database descriptions:
  - U/C: "continuously updated from the source systems".
  - U/P: "loaded nightly at 00:00", with the load time stored in a `_meta` collection.
  - A real warehouse documents its schedule, and hiding it would make U/P look worse than it is.
- **No warehouse-plus-live-sources arm.** An agent allowed to query the live systems could skip the
  warehouse and read them directly, which is arm D. And where live access is not allowed, the mixed
  setup does not exist. It was considered and dropped (decided 2026-10-09).
- **F/P** (nightly copies of the six fragmented systems) completes the README's 2×2. It is deferred:
  the static pilot already isolates the effect of consolidation, and F/P adds a fifth arm without
  a new question. It is cheap to add later.
- **Tool budgets are the same in every arm:** DAB's four tools, with the same turn limit and the
  same 10-minute wall-clock cap per episode (new; §5).

## 4. Timeline

- **Rewind.**
  - The live CBS starts as of the end of 2026-06-30: transactions dated 1 July or later are removed.
  - Each account's balance is set to the `balance_after` of its newest remaining transaction.
  - An account whose history starts after 30 June is set to its oldest transaction's
    `balance_after − amount`.
  - The other five systems are static in phase 1 and stay as they are.
- **Replay** runs from 2026-07-01 00:00 to 2026-07-06 24:00, simulated time.
  - **Writer:** for each transaction, in one Postgres transaction, it inserts the row and updates
    the account's `available_balance`. That is a real concurrent writer, as the README requires.
  - **End check:** at the end, the sources equal the published dataset. This checks the rewind and
    the replay.
- **Time of day (generated, and disclosed as such).**
  - Each transaction gets a time between 08:00 and 22:00 IST, drawn deterministically from a hash
    of its `reference_id`.
  - Same-day transactions on one account are ordered by their `balance_after` chain, so the times
    respect the ledger.
  - The time decides only when the writer posts the transaction. It is not stored: agents see the
    date, as in the dataset (as built; §13).
- **Clock.**
  - One simulated day lasts 64 real minutes: a 2-minute night (the warehouse loads in the middle of
    it) and 62 minutes for business hours, 08:00-22:00. Nothing is written at night, so it is
    compressed (as built; §13).
  - At that rate the writer averages about 8 transactions per real minute (465-624 a day on 1-5 July).
  - The clock is a parameter. Lag is always reported in **real seconds** and warehouse staleness in
    **simulated hours**; neither is converted into the other.
- **Nightly load.** At each simulated 00:00 the loader copies `views.bank_customer` into
  `bank_warehouse.customer` and writes `_meta.loaded_at`.
- **Reset between passes.**
  - The sources are restored to the rewind state, and Tapstate is restarted from a fresh snapshot.
  - Its view is checked against the rewind state for 944 of 944 customers before the clock starts.

### One live CBS

The direct-source arm and Tapstate must read **the same** database, or "current" means two
different things.

- Today, agents read `bank_cbs` (columns of type DATE), while Tapstate reads a copy, `banksrc.cbs`,
  with those dates stored as text. The copy works around tapstate/tapstate#681, where DATE values
  can't be read in a `js` step.
- For this extension, `bank_cbs` itself gets the text dates, and Tapstate's CBS source points at
  it.
- The agent-visible change (ISO date text, which sorts and compares correctly in SQL) applies to
  every arm and is disclosed.
- The other five systems keep their current copies, which is fine while they are static.

## 5. Questions, checkpoints and scoring

**Questions** (24 per pass, frozen before any run, answers computed from the replay).

| Kind | n | Example | Systems |
|---|---:|---|---|
| Changed, one system | 6 | current balance of SB…; reference id of its most recent transaction | CBS |
| Changed, cross-system | 6 | can savings cover this month's EMI and premium? | CBS + lending + insurance |
| Changed, customer view | 4 | total balance, or total debited today, across all of a customer's accounts | CBS |
| Unchanged controls | 6 | the same kinds, about accounts untouched since the last load | as above |
| Portfolio | 2 | savings accounts below ₹100,000 right now; total balance of HNI customers | CBS; CBS + CRM |

- A **changed** question is about an account with a transaction between the last nightly load and
  the checkpoint. On a changed question the warehouse answer differs from the current answer by
  construction; selection rules check that.
- **Unchanged controls** confirm the warehouse is not penalized elsewhere and keep the comparison
  honest.

**Checkpoints.**

- Each question has a fixed simulated time between 1 and 5 July, at least 12.5 real minutes apart.
  Every arm starts that question at the same checkpoint, in parallel: three agents at a time.
- **Prompt:** the question carries the current time in every arm, for example "Current time:
  2026-07-03 14:20 IST."
- **Quiet window:** the accounts a question is about have no event from `margin` before the
  checkpoint to the end of the episode.
  - `margin` is 60 real seconds, a parameter compared against the measured lag.
  - Episodes are capped at 10 real minutes. In the static pilot, p95 run time was 230–340 s, with
    one outlier at 1,428 s.
  - The answer therefore can't change while an agent is answering, even though the writer keeps
    running on every other account.
- **Portfolio questions** span every account, so no quiet window is possible. The writer and the
  clock pause for those episodes, the same pause in every arm, and the pause is disclosed.

**Scoring.**

- Answers are scored against the oracle state at the checkpoint, using the answer checker from the
  static pilot (`answer_check.py` v2).
- A wrong answer is classified as **stale** if it matches the oracle state at the last nightly
  load, and **other** otherwise.
- An answer that states it reflects last night's load is recorded as **disclosed staleness**. For
  accuracy it counts as wrong, and it is reported separately. The README counts a correct
  escalation as no error, but a warehouse answer to "what is the balance now" is not an escalation.
- A U/C stale read caused by lag longer than `margin` counts as wrong. It is never retried or
  excused.

## 6. Measurements

- **Accuracy:** pass@1 per arm, separately for changed questions, unchanged controls and portfolio
  questions, with bootstrap intervals clustered by question (as in the static pilot).
- **Stale-read rate** per arm: stale answers as a share of all answers.
- **Commit-to-readable lag** for U/C, at p50, p95 and p99, measured over the whole replay and not
  just at checkpoints:
  - the writer logs each commit's wall-clock time;
  - a probe watches a MongoDB change stream on `views.bank_customer` and records when the account's
    balance first equals the new `balance_after`.
- **Cost:** tokens, turns, duration and API-equivalent cost per correct answer, as in the static
  pilot.
- **Disclosed-staleness rate** for U/P: on changed questions, how often the answer says it reflects
  the last load, rather than stating a stale value as current.
- **Source load:** queries sent to the live systems, nonzero only in the reference arm D.
- **Stress test (no agents).** Replay all 7,745 transactions in a few minutes and measure lag under
  load. It is reported separately, because the main run's write rate (about 8 per minute) is far
  below a real bank's.

## 7. Expected outcomes, and what would contradict the claim

- **D and U/C:** both near the ceiling on every kind of question, with U/C using several times
  fewer tokens. Contradicted if U/C's lag regularly exceeds `margin`, or if U/C is less accurate
  than D on changed questions.
- **U/P:** correct on unchanged controls and wrong on changed questions, almost all of them stale
  in the sense of §5. The open result is how often it discloses staleness rather than stating a
  stale value as current. That holds for this model and this prompt only.
- **Contradicting the claim:** if U/P were about as accurate as U/C on changed questions, the
  changed-question selection would be broken. That is checked before any run, since a changed
  question's warehouse answer must differ from its current answer.

## 8. Fairness rules

- The same model, prompts, tools, turn limit and wall-clock cap in every arm.
- The same checkpoint and the same writer state for every arm on a question.
- No arm gets reference answers or hints the others lack. Each arm is told its own freshness
  truthfully.
- Selection rules, questions, clock, `margin` and the warehouse schedule are frozen before the
  first scored pass.
- The pilot passes used to tune any of these are reported and excluded.

## 9. Run plan

- **Model:** Claude Opus 5.5 on the subscription, as in the static pilot.
- **Runs:** 3 arms × 24 questions × 3 passes = 216 (decided 2026-10-09). A pass takes about 5.5
  hours (five simulated days of 64 minutes, plus the rewind and the end check), so **about 17
  hours** in total, unattended.
- **Build steps:**
  1. Rewind and replay library, with the end-state check against upstream and unit tests.
  2. Generated `posted_at` times and the oracle (state at any simulated time; state at the last
     load).
  3. Question generator with quiet-window and changed/unchanged selection rules, frozen to
     `freshness_questions.json`.
  4. Writer, nightly loader and lag probe as separate processes, plus the U/P DAB dataset.
  5. Timeline driver: reset, verify, start the clock, and at each checkpoint start the three arms
     through `run_claude_arms.run_one`.
  6. Dry run of one compressed pass with scripted answers (no model), then one real pass as a
     smoke test, then the scored passes.
  7. Analysis script and a results section in this document.

## 10. Limitations

- Transactions and balances are real dataset values. Their times of day, the replay speed and the
  nightly schedule are generated or chosen, and are reported as such.
- Only the CBS changes in phase 1. Questions that depend on changes in the cards, CRM, payments or
  insurance systems need phase 2.
- Everything runs on one machine, with a single Tapstate server and a low write rate. That says
  nothing about production latency or uptime.
- The questions are read-only. The README's main metric, safe completion of tasks that write (for
  example blocking a card), needs the tau-bench task harness, not DAB.
- One model. Opus 5.5 was at the accuracy ceiling in the static pilot, so how often U/P agents
  disclose staleness may differ for other models.
- The claim assumes agents may not query the live systems (§1). Where they may, the comparison is
  with D, which the static pilot already covered.

## 11. Phase 2 (outline)

- **Generated changes to other systems:** card freezes and unfreezes, service requests opened and
  resolved, and mandates paused. They are generated by the pinned simulator's own banking actions
  under its rules, on a reproducible seeded schedule shared by every arm.
- **A real blocker first:**
  - Agents currently read cards, payments, lending and insurance as SQLite or DuckDB files, while
    Tapstate reads Postgres copies of them.
  - For those systems to change live, both sides have to read one store. That means moving them
    into engines that Tapstate captures changes from, such as Postgres schemas, which changes what
    setup A looks like.
- **Write tasks** with revalidation at write time, through the tau-bench harness (README backlog
  B5).

## 12. Decisions (2026-10-09)

| Decision | Outcome |
|---|---|
| ~~Include U/P+S~~ | Decided 2026-10-09: no. Agents without live access are the realistic case (§1) |
| Simulated day length | 60 real minutes (as built, 64 including a 2-minute night) |
| Passes | 3 |
| Portfolio questions with a writer pause | Keep 2, disclosed. The clock stops at the checkpoint, and agents start `MARGIN_S` later: the same catch-up time as any other question's quiet margin |
| One live CBS with text dates in every arm | Yes, until tapstate/tapstate#681 is fixed |

## 13. As built

Code is in `dab/freshness/`; tests are in `dab/tests/test_freshness.py`.

| File | Role |
|---|---|
| `timeline.py` | the clock, the events and their generated times, the checkpoints, and the oracle (`state_at`) |
| `fresh_questions.py` | builds the 24 questions; `freshness_questions.json` is the frozen record |
| `fresh_check.py` | scoring: correct, stale or other; disclosure of staleness |
| `make_fresh_datasets.py` | the three DAB datasets, `query_bank_fresh_{direct,live,warehouse}` |
| `setup_live_cbs.py`, `apply_tapstate.sh` | make `bank_cbs` the one live CBS and point Tapstate at it |
| `driver.py` | one pass: rewind, writer, nightly loader, mirror with lag probe, checkpoints, end check |
| `analyze_fresh.py` | the results tables |

Changes from the design above, all made before any model run:

- **Arm names are D, UC and UP.** The harness already uses `S` for the stale copies of an earlier
  experiment, so the direct-source arm is `D`.
- **U/C reads a mirror, `bank_live`.** Tapstate writes every view into one shared `views` database,
  which also holds other projects' views, so an agent pointed at it would see them. A change stream
  copies `views.bank_customer` into `bank_live.customer` as it changes. The lag is measured at the
  mirror, so it includes this hop. The warehouse load copies the same view, so U/C and U/P serve
  identical documents at each load.
- **Fixed a pipeline bug that only deletes expose.** The pipeline's `js` steps computed the nest
  join key on a row's new values only. A delete carries only the old values, so a deleted
  transaction stayed in its account's array; this showed up in testing, held for 120 s. Every
  per-table step now maps the old values too. Deletes now appear in 0.16 s and inserts in 0.57 s.
  The static pilot never deleted anything, and the view still equals upstream for 944 of 944
  customers after the fix.
- **`bank_cbs` dates are text and its tables have `REPLICA IDENTITY FULL`.** That way a delete's
  change event carries the whole row.
- **Generated times are not stored.** Agents see transaction dates, as in the dataset. So "most
  recent transaction" questions avoid accounts whose two newest transactions share a date.
- **"Transactions posted today" became "reference id of the most recent transaction".**
  - No account has two same-day transactions with a quiet window around a checkpoint.
  - A count of 1 is matched by stray numbers, such as the 01 in 2026-07-01.
  - An id is exact, and a stale answer names a different id.
- **Validators drop dates and times before matching numbers.** Every question states the current
  time, and answers repeat it.
- **Bank-wide savings threshold: ₹100,000.** At ₹10,000–50,000 the count did not change during
  the day.
- **Consolidated documents keep transactions in reference-id order**, as setup C did. An agent
  wanting the newest transaction sorts by date.
- **The wall-clock cap kills the episode.** A capped episode has no answer and no token count; the
  results report the count of capped episodes separately.

Commands, run from `dab/freshness/` (Tapstate commands need `TAPSTATE_PASSWORD`; model runs run
under `sg docker` with `DOCKER_HOST` set, as in the static pilot):

```shell
python setup_live_cbs.py && ./apply_tapstate.sh            # once
python fresh_questions.py --write && python make_fresh_datasets.py ~/DataAgentBench
python driver.py ~/DataAgentBench --pass 0 --scripted --speed 60      # dry run, no model
python driver.py ~/DataAgentBench --pass 1 --model claude-opus-5-5   # a scored pass, ~5.5 h
python analyze_fresh.py ~/DataAgentBench cc-claude-opus-5-5_p1 cc-claude-opus-5-5_p2 cc-claude-opus-5-5_p3
```

## 14. Results: core set (Opus 5.5, 3 passes, 2026-10-09/10)

The three passes ran back to back at a 1× clock, from 15:21 to 07:00 UTC. Every end check passed
and no pass needed a rerun. None of the 216 episodes hit the wall-clock cap or a usage limit. Full
tables: [`dab/freshness/RESULTS_opus.md`](dab/freshness/RESULTS_opus.md). Decisions made while
running: [`dab/freshness/RUN_LOG.md`](dab/freshness/RUN_LOG.md).

| Arm | All 24 (72 runs) | Changed (48) | Control (18) | Portfolio (6) | Median tokens |
|---|---:|---:|---:|---:|---:|
| D: live sources (reference) | 1.000 | 1.000 | 1.000 | 1.000 | 25.3k |
| UC: Tapstate, continuous | 0.986 | 0.979 | 1.000 | 1.000 | 16.6k |
| UP: nightly warehouse | 0.250 | 0.000 | 1.000 | 0.000 | 18.2k |

- **Freshness decides the warehouse's accuracy.** UP is right exactly when nothing has changed
  since the load (18/18 controls) and never otherwise (0/54). UC matches the live sources.
- **UC's one miss is a protocol miss, not a stale answer.** In UC q16 pass 2, the correct figure was
  in the agent's final text, but it never called `return_answer`.
- **Warehouse errors are disclosed, not silent.** All 54 wrong UP answers either stated the load time
  or declined to give a figure:
  - 50 gave the value as of the load.
  - 4 declined (today's debits, where the warehouse holds none for the day).

  The cost of a nightly warehouse here is answers the user can't act on, not confident wrong ones.
- **Cross-system vs single-system.** Only 9 of the 24 core questions need more than one system; on
  them, D's cost roughly doubles while UC's doesn't:

  | | D median tokens / turns | UC median tokens / turns |
  |---|---:|---:|
  | Cross-system (9 questions, 27 runs) | 34.7k / 7 | 16.3k / 3 |
  | Single-system (15 questions, 45 runs) | 18.1k / 4 | 16.8k / 3 |

  The largest gap is q19 (HNI total balance, core banking + CRM): 80–124k tokens on D, against
  about 28k on UC. Two portfolio questions are too few to generalize from. That, and the core set's
  bias toward single-system questions, is why the cross-system set (§15) was added.
- **Tapstate's freshness.**
  - Commit-to-readable lag over 6,924 transactions at the clock rate: p50 0.54 s, p99 0.97 s,
    max 1.06 s.
  - No question's accounts were written during an episode.
  - All 15 nightly loads ran after the view had caught up.
- **Stress.** The end-of-pass replay wrote 1,356 transactions at about 300–340 per second. Tapstate
  made them readable at about 100 per second, so the backlog peaked at 9–11 s of lag. That is
  throughput headroom for a burst, not lag at this bank's real write rate.

## 15. Cross-system set (questions 25–48)

`FRESH_SET=xs` selects it: frozen record `dab/freshness/freshness_questions_xs.json`, logs in
`dab/freshness/runs/xs/`. It uses the same timeline, slots and rules as §5, with 14 changed, 6
control and 4 portfolio questions. Every question joins core banking's live balances with at least
one other system, through that system's own key format:

| Template | Systems | Question |
|---|---|---|
| mandate_cover | core banking + payments | savings balance after each active mandate on it debits once |
| spend_power | core banking + cards | total balance plus the available limit on active credit cards |
| net_position | core banking + lending | total balance minus total loan outstanding |
| emi_premium | core banking + lending + insurance | savings balance after one EMI per loan and one premium per policy |
| obligations | core banking + lending + insurance + payments | the above plus each active mandate on the account |
| affluent_total (portfolio) | core banking + CRM | total balance of AFFLUENT-segment customers |
| home_loan_total (portfolio) | core banking + lending | total balance of home-loan borrowers |
| mandate_accounts_total (portfolio) | core banking + payments | total balance of accounts with an active mandate |
| credit_card_total (portfolio) | core banking + cards | total balance of active credit card holders |

Run: `FRESH_SET=xs python fresh_questions.py --write && FRESH_SET=xs python make_fresh_datasets.py
~/DataAgentBench`, then the driver and analyzer as in §13 with `FRESH_SET=xs` set.

### Results (Opus 5.5, 3 passes, 2026-10-10)

The scripted dry run caught one scoring bug before any model ran: negative net positions, which
answer_check can't read. They are now stored by magnitude (RUN_LOG 9). After that the dry run was
exact. The three passes ran from 07:15 to 23:05 UTC. Every end check passed and no pass was rerun.
None of the 216 episodes hit the cap or a usage limit. Full tables:
[`dab/freshness/RESULTS_opus_xs.md`](dab/freshness/RESULTS_opus_xs.md).

| Arm | All 24 (72 runs) | Changed (42) | Control (18) | Portfolio (12) | Median tokens | Median turns |
|---|---:|---:|---:|---:|---:|---:|
| D: live sources (reference) | 0.958 | 0.929 | 1.000 | 1.000 | 33.7k | 7 |
| UC: Tapstate, continuous | **1.000** | **1.000** | 1.000 | 1.000 | **17.6k** | **3** |
| UP: nightly warehouse | 0.250 | 0.000 | 1.000 | 0.000 | 18.8k | 4 |

Median tokens per template, over 3 passes:

| Template | Systems | D | UC | UP | D / UC |
|---|---:|---:|---:|---:|---:|
| mandate_cover | 2 | 24.5k | 15.9k | 18.0k | 1.5× |
| obligations | 4 | 28.4k | 18.8k | 21.1k | 1.5× |
| emi_premium | 3 | 34.0k | 16.6k | 20.8k | 2.0× |
| net_position | 2 | 34.3k | 16.1k | 17.3k | 2.1× |
| spend_power | 2 | 40.2k | 24.2k | 18.8k | 1.7× |
| credit_card_total (portfolio) | 2 | 93.1k | 29.0k | 23.1k | 3.2× |
| mandate_accounts_total (portfolio) | 2 | 103.4k | 21.2k | 22.8k | 4.9× |
| home_loan_total (portfolio) | 2 | 103.8k | 22.5k | 23.2k | 4.6× |
| affluent_total (portfolio) | 2 | 107.9k | 27.6k | 22.6k | 3.9× |

- **UC is the only arm right on every question: 72/72.** D missed 3 of 72. Each miss was a
  protocol miss: the agent's final text had the correct figure, but it never called
  `return_answer` (q40 pass 1, q42 pass 2, q45 pass 3). On a cross-system question the live
  sources take 7 turns, against 3 for UC. That leaves more room to finish without submitting, but
  three misses are too few to say so firmly.
- **Cost.** UC used about half of D's tokens per question (17.6k against 33.7k), and fewer than half
  the turns.
  - On a per-customer question, D costs 1.5–2× as much.
  - On a bank-wide question (portfolio), D costs 3–5× as much: about 100k tokens against about 25k.
    The agent has to pull every account from core banking, map another system's key format onto
    it, and aggregate. UC filters one collection.
  - The number of systems doesn't predict the gap. The 4-system obligations question costs D less
    than the 2-system spend_power question. Volume (one customer or the whole bank) matters more
    than breadth.
- **The warehouse is exactly as good as its freshness.** It was right on 18/18 controls and 0/54
  otherwise, with the same pattern as the core set. All 53 wrong answers it submitted disclosed the
  load time; the 54th (q32 pass 1) was never submitted. The disclosure regex was widened four times
  during the runs, each time to a phrasing that was plainly a disclosure (RUN_LOG 5, 6, 10, 11, 12).
  The figures above use the final regex for both sets.
- **Tapstate's freshness.**
  - Commit-to-readable lag over 6,924 clocked transactions: p50 0.54 s, p99 0.97 s, max 1.06 s.
    That matches the core set.
  - All 15 nightly loads ran after the view had caught up.
  - No question's accounts were written during an episode.
- **Stress.** The end-of-pass replays drained at 70–73 transactions per second, against 96–101 per
  second in the core passes, with the same writes (peak lag 13–14 s). The cause was not
  investigated.

## 16. Summary

Over 432 episodes on two question sets, continuously updated consolidated state (UC) did the
following:

- It matched the live systems' accuracy: 143/144 correct, against 141/144 for the live sources,
  whose misses were all unsubmitted answers. The nightly warehouse got 36/144, and was correct only
  when nothing had changed since its load.
- It did so at about half the tokens and turns on cross-system questions.
- On bank-wide cross-system questions, it cost a quarter to a fifth of the tokens.

The warehouse's failures were honest. It stated the load time, or declined, in every submitted
wrong answer. In this setting its cost is unusable answers, not confident wrong ones.

The claim holds for this dataset and model under the stated deployment assumption: agents get a
consolidated store, not live access. Continuously updated state keeps a consolidated store's
efficiency without a nightly warehouse's staleness.

The limits in §10 still apply:
- one bank, one model and simulated time
- the D arm is a reference only (live access is the thing the deployment assumption rules out)
- the warehouse arm's honesty depends on its description telling the agent when it was loaded.

