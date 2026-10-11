# Freshness extension: run log and decisions

Decisions made while running unattended (the user asked on 2026-10-09 to complete the test without
stopping and to record decisions). Newest last.

1. **Scripted dry run first (pass 0, 60× clock, no model).** It found two bugs in the scripted
   agent only (transaction order and the portfolio helper); both were fixed and the pass rerun. On
   the rerun:
   - D and UC were correct on all 24 questions.
   - UP was correct on all 6 controls and stale on all 18 other questions.
   - Lag over 2,309 writes: p50 0.55 s, p99 0.99 s, max 1.74 s.
   - At the end, bank_cbs and bank_live both equal upstream.
2. **Model smoke test before the scored passes:** questions 1–3 at a 4× clock, labeled pass 90 and
   excluded from results. At 4× the quiet windows don't hold for long episodes, so it checks
   plumbing only (MCP tools, sandbox, answer capture, scoring).
3. **Smoke test passed** (pass 90, questions 1–3, 4× clock):
   - D and UC were correct on all 3; UP was correct on the control and stale on both changed questions.
   - The end check passed.
   - Every UP answer disclosed the load time ("I can't give you the live balance … last loaded at
     00:00") and then gave the value as of the load. That points to the warehouse's cost being
     non-answers, not confident wrong answers; the scored passes measure how consistently.
4. **Scored passes 1–3** run back to back at a 1× clock (the 3 decided passes), with Opus 5.5 on the
   subscription and a 10-minute cap per episode.
   - A pass that fails its end check, or crashes, is rerun once.
   - An episode refused by a usage limit can't be retried at its checkpoint; it is counted as not run
     and reported.
5. **Pass 1 done** (15:21–20:34 UTC, end check passed, no failures). The disclosure regex missed one
   UP answer that plainly disclosed ("That is the last time the data was loaded", q19). Fixed:
   - The regex now also matches "was/were loaded", "refreshed once a night" and "not … right now".
   - `analyze_fresh.py` now recomputes disclosure from each stored answer, so every pass is scored
     by the same regex. Verdicts are unaffected.
   - The two UP "other" answers (q8, q17, today's debits) are abstentions. The warehouse had no
     debits for that day, so the stale answer would be ₹0; the model declined to give a figure and
     said why.
6. **Pass 2 done** (20:34–01:47 UTC, end check passed, no failures).
   - UC q16 was scored "other": the agent wrote the correct total (₹8,35,150) as its final text but
     never called `return_answer`, so no answer was submitted. It stays scored wrong, the same DAB
     protocol rule as in the static pilot, and is reported separately as a protocol miss, not a
     freshness failure. It is the only episode of 144 without a submitted answer. The analyzer now
     lists such episodes.
   - The disclosure regex also missed "This uses data loaded on …" (UP q14), so it now matches
     "loaded on" too. Disclosure is rescored for every pass.
7. **Cross-system split and follow-up**, on the user's request to focus on cross-system questions.
   - Only 9 of the core 24 questions need more than one source system (emi, emi_premium, hni_total),
     confirmed by arm D's tool logs. `analyze_fresh.py` now reports cross-system and single-system
     groups, using each question's `systems` field.
   - A second set, "xs" (questions 25–48, `FRESH_SET=xs`), has 24 questions that all join core
     banking's live balances with at least one other system. It keeps the same slots and rules: 14
     changed, 6 control, 4 portfolio.
     - Per-customer templates:
       - mandate_cover: core banking + payments
       - spend_power: + cards
       - net_position: + lending
       - emi_premium: + lending + insurance
       - obligations: + lending + insurance + payments
     - Portfolio questions (bank-wide totals), each a total balance:
       - AFFLUENT segment: + CRM
       - home-loan borrowers: + lending
       - accounts with an active mandate: + payments
       - active credit card holders: + cards
   - Segment is held only in CRM; the core banking `customers` table has no segment column.
   - The xs set has its own frozen file (`freshness_questions_xs.json`) and runs directory
     (`runs/xs/`). Query directories 25–48 don't collide with the core set's run logs.
   - The core set and its frozen file are unchanged. 30 tests pass, 5 of them new.
   - The xs runs start after core pass 3: a scripted dry run first (it must be exact), then 3
     scored passes.
8. **Core set done** (pass 3: 01:47–07:00 UTC, end check passed). Results are in FRESHNESS.md §14 and
   RESULTS_opus.md.
9. **The xs dry run caught a scoring bug.** All three net_position questions with a negative answer
   (q30, q37, q46) failed in every arm. The cause: answer_check reads unsigned numbers, so
   "−₹34,083.33" can never match.
   - The shared checker was left unchanged.
   - net_position answers are now stored by magnitude; agents state a shortfall as "−₹x", "a deficit
     of ₹x" or "₹x short" alike.
   - The same customers were chosen, since only the values changed. A new test requires every money
     answer to be unsigned in both sets.
   - The xs run was relaunched at 07:09 UTC.
10. **xs dry run exact; xs pass 1 done** (07:15–12:32 UTC, end check passed).
   - Two more protocol misses, where the agent never called `return_answer`:
     - D q40: the correct ₹48,545.32 was in its final text.
     - UP q32.
     They are scored as "other", as in decision 6.
   - The disclosure regex missed "from the warehouse load at …" (UP q36, q40). It now matches
     "warehouse load", with a test added. The core set's figures are unchanged (54/54).
11. **xs pass 2 done** (12:32–17:48 UTC, end check passed).
   - A third protocol miss: in D q42 the correct ₹1,42,201 was in the agent's final text, but it
     never called `return_answer`.
   - The disclosure regex missed "the last data load" (UP q41, q45). "last … load" now matches any one
     word in between, with a test added. The only wrong UP answer that doesn't disclose is the
     unsubmitted q32.
   - The end-of-pass stress drain is slower in xs passes (70–73 per second, against 96–101 in the core
     passes). The replayed writes are identical. The cause was not investigated.
12. **xs pass 3 done** (17:48–23:05 UTC, end check passed); all runs complete.
   - A fourth protocol miss, D q45: the correct ₹5,57,700 was in the agent's text, but it never
     called `return_answer`.
   - The disclosure regex missed "loaded today … at 00:00 IST" (UP q40), so it now matches "loaded
     today, yesterday or last night", with a test added.
   - Both results files were regenerated with the final regex (RESULTS_opus.md,
     RESULTS_opus_xs.md). The write-up is FRESHNESS.md §14–16.
