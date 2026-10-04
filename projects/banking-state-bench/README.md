# Banking Operational State Benchmark — project charter

Status: initiated 2026-10-04; no experimental banking results yet.

## Research question

At the same business-safety requirement, does current, consolidated operational state let a banking agent complete more legitimate tasks without human intervention, and at a lower complete cost, than competent alternatives?

Do not optimize for an impressive multiplier. Failure to improve over direct source tools is a valid result. This project is separate from the launch milestone.

## Candidate sources

1. **TauIndianBankBench**, NPCI AI Research Team: https://github.com/npci/TauIndianBankBench and https://huggingface.co/datasets/NPCI/tau-indian-banking. The current README describes 1,000 synthetic tasks, 50 scenario families, 33 banking tools plus human transfer, and ACTION + DB scoring. Source material includes accounts, transactions, cards, deposits, loans, mandates, and service requests. This is a new benchmark, not an independently established banking certification.
2. **τ-Banking / τ-Knowledge**, Sierra Research: https://arxiv.org/abs/2603.04370 and https://github.com/sierra-research/tau2-bench. A second validation target; separate knowledge retrieval failures from state failures. Use the current corrected release and pin its exact commit, data revision, scorer, and task split.
3. **BPI Challenge 2017**: https://research.tue.nl/nl/datasets/bpi-challenge-2017/ . Optional historical loan-event replay, not a counterfactual action simulator or a modern account ledger.

Source descriptions were checked on 2026-10-04. Confirm provenance, data/code license, dataset availability, and usable upstream tests before choosing. Record changes rather than assuming current descriptions remain true.

## First implementation milestone

Deliver a runnable, local, no-paid-API smoke test before bulk experiments:

1. Inspect the current upstream code and data. Pin versions and hash inputs. Do not redistribute upstream datasets unless terms permit it.
2. Reproduce original fixture loading and deterministic reference-action scoring on a small, transparently selected task set, using scripted trajectories or a supported mock agent. Do not invent an LLM score.
3. Audit which tools revalidate state on write and which task families depend on mutable facts. Select a representative pilot set, not only tasks engineered to fail on old snapshots; report immutable controls too.
4. Define a read-only state adapter and an authoritative write adapter. Keep authentication, authorization, policy retrieval, user confirmation, idempotency, and write-time conditional validation identical across experimental arms.
5. Implement one clearly labeled dynamic-extension smoke test: an external process changes business state during the episode; a stale reader sees an earlier version and a current reader sees the applicable version. Use rules supported by the pinned simulator. Log events, read versions, intended actions, accepted/rejected actions, and ground truth.
6. Provide executable tests, commands, a findings note, and a bounded paid-pilot proposal. Do not start a metered API batch without the human choosing models, simulator, and budget.

If upstream cannot run locally, document the concrete blocker and build only a labeled adapter-contract fixture; do not claim to have reproduced the benchmark.

## Experimental arms

| Arm | Representation | Freshness |
|---|---|---|
| Direct-source control | Original competent source tools | Current source reads |
| F/P | Fragmented source-shaped state | Periodic / lagged |
| F/C | Fragmented source-shaped state | Continuously updated |
| U/P | Unified entity state | Periodic / lagged |
| U/C | Unified entity state | Continuously updated |

The 2×2 design separates consolidation from freshness. The direct-source control answers why an extra infrastructure layer is justified. Reduce to a smaller pilot only with an explicit explanation. Do not give only one arm golden documents, reference actions, answers, or extra policy knowledge. Keep semantically equivalent fields and tool budgets.

Lag is an observed or explicitly simulated quantity, not a label. CDC-based claims require a real concurrent writer, state updater, and reader; replayed or reloaded snapshots do not establish live CDC latency or uptime.

## Dynamic correctness contract

- Specify action preconditions from simulator policy, not guessed banking law.
- Record effective source commit/version, read version, decision time, action time, and update/recovery times.
- An external event schedule must be reproducible and shared across paired arms. Use event barriers when trajectories take different numbers of calls; disclose the scheduling choice.
- Original reference-action replay assumes a deterministic starting environment. New external events need a versioned, time-aware oracle and explicitly updated scoring; do not reuse an invalid static gold hash.
- Distinguish read-time correctness, unsafe action attempts, source-side rejected attempts, and actual accepted incorrect mutations.
- Current state is not a distributed transaction guarantee. Preserve write-time revalidation and explicitly handle changes between read and write.
- A correct refusal or required escalation is not an error. Refusing all legitimate requests is not success.

## Metrics and acceptance

Primary: safe legitimate task-completion rate at a declared risk threshold. Report task denominators, correct refusal/escalation, human handoff, rejected writes, unsafe accepted writes, and repeated-trial reliability.

Secondary: commit-to-readable p50/p95/p99, stale-read rate, recovery and manual interventions, source calls/load, latency, tokens, actual API spend, pipeline build/maintenance work, and infrastructure costs. Per-success cost includes failures. Do not turn synthetic dollar amounts into real avoided losses.

Freeze transformations, selection rules, prompts, policies, and simulator before holdout evaluation. Cluster uncertainty by task/scenario rather than treating repeats as independent customers. Include changed and unchanged cases and report coverage plus stress-test results separately. Seek clean-machine reproduction before claiming independent reproducibility.

## Branch and operational boundary

Use a separate `codex/` branch and worktree. Own only banking-project files on that branch. The publication task owns `release/`; do not rewrite its narrative or import pilot results into it. Treat `/Users/tjworks/dev/dab-bench-runs` as a read-only source of prior work. No production banking access, real financial actions, paid bulk calls, GitHub pushes, website deployment, or automatic messages to other tasks are authorized by this charter.

## Backlog

- [ ] B0: pinned source/license/provenance audit.
- [ ] B1: original fixture and reference scorer reproduced locally.
- [ ] B2: mutable-state dependency and write-protection audit.
- [ ] B3: adapter contract plus one dynamic-extension test.
- [ ] B4: bounded, priced pilot proposal with held-out tasks and controls.
- [ ] B5: human-approved API pilot, continuous-update experiment, independent reproduction.
