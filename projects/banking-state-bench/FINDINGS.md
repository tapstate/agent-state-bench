# Phase 1 findings

Checked 2026-10-04. No paid model calls were made and no benchmark score is claimed.

## Source, licence, and version

Primary source: NPCI `TauIndianBankBench`, pinned to commit
`dd4ecc7f5e1d0454243eb4cae8c03d2b496ca00f` (package version 1.0.0). The
repository `LICENSE` and package metadata say MIT. Its README says the domain,
tasks, and data share that licence and that all banking records are synthetic.
The fetched source archive was about 318 KiB compressed / 1.2 MiB expanded.
Its SHA-256 is `0ce01fe9c61ae3419d71a4a9987613ef8317373c563d140581d87cfc47b652f7`.

The data is separately hosted at `NPCI/tau-indian-banking`. API, Git HEAD, and a
bounded direct request for `tasks.json` did not return usable data from this
environment. Therefore the dataset revision and file hashes remain unpinned,
upstream fixture loading and DB replay have **not** been reproduced, and B1 is
blocked on data availability. Run `scripts/fetch_data.py --revision <commit>`
only after a dataset revision can be resolved; do not use an unpinned `main`.

Fallback: Sierra `tau2-bench` is MIT. Use release >= 1.0.1 for corrected
`banking_knowledge` grading; its changelog states results before and after 1.0.1
are not comparable. It tests knowledge retrieval as well as state and is not a
drop-in operational-ledger substitute.

## Scoring reproduction status

The local `score_reference_actions` contract reproduces the inspected upstream
ACTION semantics: all gold actions must occur; extra reads are allowed; every
extra WRITE fails write purity. The upstream DB scorer creates a fresh gold
environment, replays gold actions, and compares canonical DB hashes. Without the
dataset, only ACTION contract behavior is tested locally; original task fixture
loading and DB equality are not claimed.

## Mutable-state and write-protection audit

The upstream toolkit labels 13 tools WRITE: 11 actual mutators plus two
request-style soft writes. Read/compute tools are separate. Service methods
re-check active customer and product linkage on most writes and perform useful
state checks such as insufficient funds, already-closed deposits, already-blocked
cards, cleared cheques, and required transaction IDs.

Important boundary: user confirmation is mostly expressed in policy/prompts,
not as a `confirmed` argument enforced by the service. There is no general
expected-version/concurrency token or idempotency key at the tool boundary.
Several deterministic references reduce replay variance but are not a general
idempotency guarantee. Accordingly, a stale decision can reach a current source;
individual service checks may reject it, but there is no uniform compare-and-set
contract. Experimental adapters must not weaken upstream linkage checks and must
add confirmation/version handling consistently to every arm.

Likely mutable families include balances and deposit creation/closure, card
freeze/block/controls, mandate cancellation, cheque stops, address requests, and
service requests. Immutable controls should include calculators and stable
policy/knowledge lookups; account/card detail reads are mutable observations,
not controls.

## Custom dynamic extension

`banking_state_bench.py` is explicitly a custom adapter-contract fixture, not an
upstream benchmark result. It creates paired periodic/current readers, applies a
barriered external card-block event, and attempts a stale freeze. The source
rejects the action on expected-version mismatch while preserving authentication,
authorization, confirmation, linkage, and idempotency checks. The output logs
read versions, event versions, intended action, rejection, and ground truth.

Run:

```bash
python3 projects/banking-state-bench/banking_state_bench.py
python3 -m unittest discover -s projects/banking-state-bench/tests -v
```

## Bounded pilot proposal (requires human choice before spend)

After upstream data is available, select 12 tasks transparently: two immutable
controls; two current-but-read-only state tasks; and eight write tasks spread
across deposits, cards, mandates, cheques, profile/service requests, with both
changed and unchanged cases. Run scripted gold trajectories first across direct,
F/P, F/C, U/P, U/C arms. Then run at most two human-selected models, one trial
per task and arm (maximum 120 episodes), concurrency 1, hard provider spend cap
chosen by the human, and stop after the first 10 episodes to extrapolate cost.
Freeze prompts, transformations, event schedule, tool limits, and scorer before
the remaining episodes. Report accepted incorrect mutations separately from
unsafe attempts rejected at the source. Do not infer a benefit if confidence or
coverage is inadequate.
