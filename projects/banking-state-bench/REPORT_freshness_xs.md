# Cross-system freshness test: bank benchmark

11 October 2026. Exported from the shared report doc; the doc has a token-cost chart that this copy replaces with a pointer.

On 24 questions that each join live core-banking balances with a second system, agents on Tapstate's continuously updated state answered 72 of 72 correctly. Agents on the live source systems used about twice the tokens, and up to five times on bank-wide questions. Agents on a nightly warehouse were right only when nothing had changed since midnight: 18 of 72.

## Setup

The test replays five business days of a simulated bank (1–5 July 2026) while agents answer questions about it. Each question carries its own "current time", and the arms are asked it at that moment.

**Deployment assumption.** Enterprises give agents a consolidated store and block live access to source systems for security and load reasons. Arm D is therefore a reference point, not a deployment option.

| Arm | What the agent can query | Freshness |
| --- | --- | --- |
| D: live sources (reference) | The six source systems directly: core banking (Postgres), cards, payments, lending, insurance, CRM | Live |
| UC: Tapstate continuous | One consolidated customer collection, maintained by Tapstate change capture | Updated within about a second of each write |
| UP: nightly warehouse | The same consolidated collection, copied once a night at 00:00 IST | 8–20 hours old when asked |

- **Data.** The NPCI-style bank dataset: 944 customers, 1,306 accounts and 7,745 transactions, plus loans, policies, cards, mandates and CRM profiles. Each system keeps its own key format: for example, lending stores the customer suffix in lower case, payments prefixes the IFSC, and CRM uses a hyphen.
- **Time.** One simulated day runs in 64 real minutes, with 3,664 transactions written to core banking as the days go by. Every arm gets the same question at the same moment. Each question's accounts were chosen to have no writes from a minute before it until its 10-minute cap, so the answer can't change mid-episode.
- **Questions.** All 24 join core banking's changing balances with at least one other system:
  - 14 *changed* questions: the answer moved since the last nightly load.
  - 6 *control* questions: nothing relevant changed since the load.
  - 4 *bank-wide* (portfolio) totals: the clock pauses while they run.
- **Templates.** Per customer: mandate cover (+ payments), spending power (+ cards), net position (+ lending), EMI + premium (+ lending, insurance), all obligations (+ lending, insurance, payments). Bank-wide: total balance of AFFLUENT customers (+ CRM), of home-loan borrowers (+ lending), of accounts with an active mandate (+ payments), and of credit card holders (+ cards).
- **Runs.** Claude Opus 5.5 in Claude Code, 3 passes × 24 questions × 3 arms = 216 episodes, run on 10 October 2026. Answers are scored automatically against a fixed answer for each question's moment. Dates and times are stripped first, so the stated time can't supply a matching number.

## Accuracy

Tapstate's continuous state was the only arm right on every question. The warehouse was right exactly when nothing had changed since its load, and never otherwise.

| Arm | All 24 (72 runs) | Changed (42) | Control (18) | Bank-wide (12) |
| --- | --: | --: | --: | --: |
| UC: Tapstate continuous | **72/72** | **42/42** | 18/18 | 12/12 |
| D: live sources (reference) | 69/72 | 39/42 | 18/18 | 12/12 |
| UP: nightly warehouse | 18/72 | 0/42 | 18/18 | 0/12 |

All three of D's misses were submission failures, not wrong numbers. In each (q40, q42 and q45, one per pass) the agent's final text held the correct figure, but it never called `return_answer`, so under the benchmark's rules nothing was submitted. D needs a median of 7 turns per question against Tapstate's 3, which may leave more room for this. Three cases are too few to say so firmly.

The pass@1 95% intervals (bootstrap over questions) are 1.00–1.00 for UC, 0.93–1.00 for D and 0.12–0.38 for UP. Every pass ended with the core-banking database and Tapstate's state both equal to the expected data.

## Cost

Tapstate's continuous state used a median of 17.6k tokens and 3 turns per question; the live sources used 33.7k tokens and 7 turns.

*Chart in the doc: median tokens per question for each arm and template. The same numbers are in the per-template token table in `FRESHNESS.md` §15 and in `dab/freshness/RESULTS_opus_xs.md`.*

The gap follows how much data a question pulls, not how many systems it touches. The four-system obligations question costs the live sources less than the two-system spending-power one. On a bank-wide total, the agent on live sources has to read every account from core banking, map another system's key format onto it and aggregate; on Tapstate it filters one collection. The warehouse costs about the same as Tapstate (18.8k median): it is consolidation that saves the tokens, and in this test keeping it continuously updated added no token cost per question.

## How the warehouse fails

The warehouse never gave a wrong answer silently. All 53 wrong answers it submitted gave the figure as of the last load and said so; the 54th (q32, pass 1) was never submitted. Its description told the agent when it was loaded, and the agent used that every time.

A typical answer, for a spending-power question at 13:52 (correct answer ₹8,81,699):

> Customer CUST\_391D8014 has a total spending power of ₹8,36,699. … These figures are from the warehouse load at 2026-07-03 00:00 IST, about 14 hours before the time you gave. Any transactions or card spends made today after midnight aren't included, so the real-time figure may differ.

The cost of a nightly warehouse here is answers the user can't act on: a balance check, mandate cover or spending limit stated "as of midnight" doesn't settle a decision made in the afternoon. It is not confident wrong answers. That honesty depends on the agent being told the load time; a warehouse without that metadata was not tested.

## Tapstate freshness and throughput

A transaction committed to core banking was readable in Tapstate's consolidated state about half a second later.

| Measure | Value |
| --- | --- |
| Commit-to-readable lag, 6,924 clocked transactions over 3 passes | p50 0.54 s · p95 0.89 s · p99 0.97 s · max 1.06 s |
| Nightly warehouse loads that ran before Tapstate had caught up | 0 of 15 |
| Questions whose accounts were written during an episode | 0 |
| Stress: end-of-pass replay of 1,356 transactions with no agents | written at 214–233/s, readable at 70–73/s, peak lag 13–14 s |

The stress row is throughput headroom, not normal lag: the replay writes about 1,000 times faster than the simulated business day (about 0.2 writes per second). The same replay in the earlier core-set passes drained at 96–101 per second, with a peak lag of 9–11 s. The cause of the difference wasn't investigated.

## Limits and run notes

**What this does not show.**

- One bank dataset, one model (Opus 5.5) and simulated time. Real business-hour write rates and query mixes will differ.
- Only core banking changes during the day; the other five systems are static. So every question's freshness gap comes from balances, not from cards, loans or mandates changing.
- D is a reference, not a deployment option, under the deployment assumption above.
- Four bank-wide templates and five per-customer templates: enough to show a consistent pattern, not to estimate cost ratios precisely.

**Decisions made during the run.**

- *Negative answers.* The scripted dry run, which runs before any model, found that the answer checker can't read negative numbers. All three net-position questions with a deficit failed in every arm. Net-position answers are now stored as magnitudes, and a test keeps every money answer unsigned. After the fix, the dry run was exact.
- *Unsubmitted answers* stay scored as wrong, as in the earlier static benchmark, and are listed separately.
- *Disclosure check.* The pattern that detects "this figure is as of the last load" was widened four times during the runs, each time for wording that plainly disclosed. Every pass was rescored with the final version. Right/wrong verdicts never changed.

**Sources** (in the `banking-state-bench` project): `FRESHNESS.md` §15–16, `dab/freshness/RESULTS_opus_xs.md` (full tables), `dab/freshness/RUN_LOG.md` (decisions 7–12) and `dab/freshness/freshness_questions_xs.json` (the 24 frozen questions and their answers).
