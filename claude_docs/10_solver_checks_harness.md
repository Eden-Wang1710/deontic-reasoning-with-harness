# 10 · Solver-Checks Harness (rule document in the solver's context, verifier as a list of checks)

- **Name**: the **solver-checks harness** (working name, 2026-11-17), `AGENT=t2sc`,
  `tools/dar_terminus2_checks.py`. Third harness of the thesis after the solver-verifier
  harness (`08`) and the reader-verifier-compactor harness (`09`).
- **In one paragraph**: a terminus-2 solver gets the whole rule document in its first message
  (when the document is small), writes an answer file with four fixed sections, and must have it
  examined by a **verifier that is a list of independent checks**. Each check is a skill: it
  fills a table the skill defines, and reports findings that quote the rule text. The harness
  knows nothing about the task: it validates the shape of a report, searches the rule document
  for every quote, gates `task_complete` on the verdicts, and lets the solver either correct the
  file or rebut a finding. There is no compactor and no reader for small rule documents.
- **Status (2026-11-18)**: implemented, four smoke runs, three full runs under the cost protocol
  of `09` §10.12 (§7). SARA numeric with Qwen3.5-35B-A3B: **19/35** at 9.40 cents per task
  (reader-verifier-compactor: 17/35 at 6.51); with Qwen3.8-27B: **24/35** at 7.99 cents on the
  same price card, where the checks changed no outcome (§7.3).

## 1. Why this version

Decisions of 2026-11-17 (owner, after discussion with the PhD senior; log in `04` §8):

1. **Compaction is not the bottleneck.** In `rvc-cost1-numeric` the compactor was called in
   1 of 35 trials (0.02 cents per task, `09` §10.12). A separate compactor agent is
   dropped; a light context manager is deferred until a benchmark needs it (§8).
2. **The reader is not the bottleneck on SARA either.** The statute file has 27,196 characters
   (about 6.1k tokens). Printing it once costs less than the reader calls it replaces
   (reader: 1.75 cents per task in `rvc-cost1-numeric`), and removes the failure where the
   solver never asks for a provision it does not know exists.
3. **Most remaining errors are provisions or facts the solver never applied** (`09` §10.11).
   The verifier of `09` read one rubric list in one context and mostly confirmed the solver's
   own selection. The new verifier makes coverage a table that has to be filled row by row:
   one row for every top-level unit of the rule document, one row for every fact of the case.
4. **Generality** (owner, verbatim: "我们希望这个harness比较通用，也就是这个架构在别的benchmark
   也能用（skills具体的内容可以改）"). Nothing in the Python code may depend on the benchmark.
   What a check looks for lives in its skill; the harness only handles shape, quotes, verdicts
   and rounds. An earlier idea, to let the harness judge two process rubrics from the trajectory,
   was rejected as overfitted; the process rubrics are an optional check skill instead
   (`check_process_v1`, off by default).

## 2. Architecture

    solver (terminus-2, skill solver_v4)
      first message = rule document (if <= rule_inline_max_chars) + skill + task
      writes /app/output/answer.txt:  ## Sections / ## Facts / ## Reasoning / ## Answer
      calls  "subagent": {"name": "verifier"[, "rebuttal": "..."]}
                 |
                 v
    harness: answer-format guard -> for each check: run sub-agent -> validate shape
             -> search rule document for every quoted rule_text -> merge into one report
                 |
       check 1: section table      check 2: fact table      check 3: grounded reasoning
       (own context each; shared prompt prefix = task + rule document + report format)
                 |
                 v
    report to the solver (PASSED / NOT PASSED, numbered findings with quotes)
    gate: task_complete accepted when the latest round on the current file passed

Class chain: `Terminus2SolverChecks` → `Terminus2ReaderVerifier` → `Terminus2RubricVerifier` →
`Terminus2TruthfulMarker` → `CostLedgerMixin` → `Terminus2`. The cost ledger, the output-token
limits with one fallback to no thinking, the protocol correction for misplaced sub-agent calls
and the reader (used only above the inline limit) are inherited from `09`.

### 2.1 Rule document in the solver's context

At trial start the harness reads `rule_path` from the container. If it has at most
`rule_inline_max_chars` (60,000) characters, the text fills the `{rule_document}` slot of the
solver template `tools/templates/terminus-json-multiagent-v4.txt`, **before** the skill and the
task, and the reader is switched off. Above the limit the slot holds a note that tells the solver
to use the reader. The size limit is the only thing the harness knows about the file.

### 2.2 Checks

`checks` (default `section_table,fact_table,grounded_reasoning`) names skills under
`tools/skills/check_<name>_v1/SKILL.md`. A verifier call runs them in turn, each as a sub-agent
with its own context. The prompt of every check is, in this order: task, rule document (text, or
the path when it is too long), the shared ground rules and report format
(`tools/skills/check_report_v1/FORMAT.md`), the check's skill, and a packet with the answer file,
the check's own findings of the previous round and the solver's rebuttal. Task and rule document
come first so that all checks of a trial share a prompt prefix (prefix cache).

| Check | Table (one row per ...) | Finding when |
|---|---|---|
| `section_table` | top-level unit of the rule document: `engaged` yes/no decided from the case alone, then `answer` applied / not applied / partly | the two columns disagree |
| `fact_table` | fact stated in the case: the `provision` that uses it, found from the rule document alone, then `answer` used / not used / used differently | a provision uses the fact and the answer does not |
| `grounded_reasoning` | claim the reasoning rests on (rule claim, computation, conclusion): `holds` yes/no | a claim does not hold; arithmetic is redone with python3 |
| `process` (optional) | how the solver worked, read from its trajectory | off by default |

Both tables are filled in two passes: first from the task and the rule document, **before**
looking at how the answer file treats the row, then from the answer file. This is "plan A"
(the check sees the answer file but decides the first column first). "Plan B" (a blind table by
a sub-agent that never sees the answer) is not implemented; the smoke runs showed that plan A
does surface provisions the answer did not apply 🔶 (§6).

Every check returns the same object: `table`, `verdict` (PASS | FAIL), `findings`
(`issue`, `rule_text` verbatim, `basis`) and `notes`.

### 2.3 What the harness does with a report

- **Shape**: a result that does not validate gets one repair request; a result written at the top
  level of the reply instead of inside `"result"` is accepted (`lift`).
- **Quotes**: each `rule_text` (at least `quote_min_chars` = 12 characters, whitespace-normalised)
  is searched in the rule document. A finding whose quote is not found is marked for the solver
  as a lead, not as rule text.
- **Status per check**: `PASS`; `FAIL` (at least one finding with a quote found in the rule
  document, or a computation finding of a check whose quote is optional); `UNSUPPORTED` (FAIL
  without any supported finding: shown, does not block); `NO_REPORT`.
- **Round**: passes when every check is PASS or UNSUPPORTED. At most 3 rounds per task.
- **Caching**: a check whose last result is for the current answer file (by hash) is not run
  again unless it has findings and the solver sent a rebuttal. A verifier call that would run
  nothing uses no round.
- **Answer-format guard**: an answer file without the headings
  `## Sections, ## Facts, ## Reasoning, ## Answer` (`answer_headings`, `""` disables) is sent
  back without spending a round, at most `max_format_rejections` = 2 times.
- **Gate**: `task_complete` is accepted when a round on the answer file as it stands passed.
  Otherwise the solver is told what is missing. After the rounds are used up, or after
  `max_unverified_claims` bounced claims, the file is accepted as it stands; the finish reason
  records which (`verified`, `budget_exhausted`, `unverified_accepted`).
- **Rebuttal**: `"subagent": {"name": "verifier", "rebuttal": "<finding number>: <rule text or
  fact that defeats it>"}` leaves the file unchanged; the checks with findings run again with
  their findings and the rebuttal, and are told to withdraw what the rule text does not support.

## 3. Skills and files

| File | Role |
|---|---|
| `tools/skills/solver_v4/SKILL.md` | solver: go through every top-level unit and every fact before computing; four-section answer file; correct or rebut each finding |
| `tools/skills/check_report_v1/FORMAT.md` | ground rules and report format shared by all checks (default) |
| `tools/skills/check_report_v2/FORMAT.md` | same, with the rule on facts the case does not state rewritten (§6.3); used for SARA binary |
| `tools/skills/check_section_table_v1/SKILL.md`, `check_fact_table_v1`, `check_grounded_reasoning_v1` (`max_steps: 2`), `check_process_v1` | the checks |
| `tools/templates/terminus-json-multiagent-v4.txt` | solver template with the `{rule_document}` slot |
| `tools/dar_terminus2_checks.py` | the agent |
| `tools/dar_terminus2_multiagent.py` | `_run_tool_subagent` gained `prefix`, `validate` (one repair request) and `lift` |
| `tools/sc_run_report.py` | run summary: accuracy, finish reasons, check status by round, quote checks, repairs and fallbacks, tokens per check, first report vs reward |

A check skill's front matter: `name`, `label`, `description`, `table: required`,
`quote: required | optional`, optionally `max_steps` and `trajectory: true`.

No skill, prompt or guard contains a task, a case fact, a provision or a value (debugging
boundary of 2026-11-10, `04` §8). The solver skill and the check skills speak of "top-level
units of the rule document" and "facts stated in the case".

## 4. Settings and how to run

Agent kwargs in addition to those of `09`: `checks`, `check_format_path`,
`rule_inline_max_chars` (60000), `check_max_steps` (1 command step when the rule document is in
the prompt), `check_max_steps_file` (6 when it is not; a skill's `max_steps` raises either),
`check_max_output_tokens` (8000), `check_thinking` (true), `check_fallback` (true),
`quote_min_chars` (12), `answer_headings`, `max_format_rejections` (2).

Protocol: the cost protocol of `09` §10.12 unchanged (no wall-clock limit, cost budget
0.15 USD per task at the no-cache price of `tools/pricing/price_card.json`, `max_tokens=16384`,
T 0.7, thinking on).

    # from the repo root; vLLM must be serving (tools/serve_qwen35_35b.sh)
    AGENT=t2sc SUBSET=numeric N_CONCURRENT=8 tools/run_dar_qwen35_35b.sh direct --job-name sc-run1-numeric
    AGENT=t2sc SUBSET=binary N_CONCURRENT=8 tools/run_dar_qwen35_35b.sh direct \
        --ak check_format_path=$PWD/tools/skills/check_report_v2/FORMAT.md --job-name sc-run1-binary
    # another served model (jobs go to jobs/<MODEL>/t2sc-direct/)
    MODEL=qwen3.8-27b AGENT=t2sc SUBSET=numeric tools/run_dar_qwen35_35b.sh direct --job-name sc-run1-numeric

    # from harbor-deonticbench/
    PYTHONPATH=../tools .venv/bin/python ../tools/sc_run_report.py <job dir> --trials --findings
    PYTHONPATH=../tools .venv/bin/python ../tools/inference_cost.py <job dirs> [--price-card <card>]

## 5. Logs per trial

`<trial>/agent/rubric_verifier/round-N.json` (answer, rebuttal, every check's table, findings,
status and usage), `round-N.<check>.packet.txt`,
`<trial>/agent/trajectory.verifier-<check>-round-N.json`, and `summary.json` with the check
configuration and history (`n_format_rejections`, `n_rebuttals`, `n_free_verifier_calls`,
`rule_inline`, ...). Ledger roles are `solver` and `verifier.<check>`.

## 6. Smoke runs and what they changed (2026-11-17)

All with Qwen3.5-35B-A3B on spark-0238, jobs under
`harbor-deonticbench/jobs/qwen3.5-35b-a3b/t2sc-direct/`. Tasks were chosen among those earlier
harnesses got wrong; gold labels were read only offline.

### 6.1 `sc-smoke1` ✅ four harness problems

1. The solver wrote an answer file with only the answer line, so the checks had nothing to
   examine. Fix: the answer-format guard (§2.3) and one sentence in the solver skill.
2. The checks spent their steps on `cat`/`grep` of a rule file that was already printed in
   their prompt. Fix: the packet says that the printed material is complete, and a check gets
   1 command step (python3 arithmetic) when the rule document is inline.
3. Some replies carried the report at the top level instead of inside `"result"` and were
   rejected. Fix: `lift`.
4. The rule document came after the skill in the solver's first message. Fix: the
   `{rule_document}` slot before the skill.

### 6.2 `sc-smoke2` (numeric 10, 18, 67, 91; binary s151-b-pos, s3306-c-2-neg; stopped after two results to free the server)

- The four fixes hold ✅: four-section answer files, checks report in 1–2 model calls, no reads
  of the printed rule file.
- numeric-67, wrong under `rvc-cost1-numeric`, was solved: round 1 all three checks FAIL with
  quoted findings, the answer changed, round 2 all PASS ✅.
- numeric-10 (never solved by any harness): the fact table found a provision the answer had not
  applied, the answer changed, round 2 passed, still wrong.
- **Binary over-strictness** ✅: on s151-b-pos the solver's first answer was right; all three
  checks failed it because the case does not state a fact that a condition of the provision asks
  about ("cannot be determined"); the solver changed to the wrong answer without a rebuttal.
- No solver used a rebuttal.

### 6.3 `sc-smoke3-binary`, `sc-smoke4-binary`: the rule on facts the case does not state

- Format v1 got a ground rule during `sc-smoke2`: a fact absent from the case is never a finding
  by itself, and "it cannot be determined" is not a finding. `sc-smoke3-binary` (the same two
  binary tasks) ✅: **not enough**. On s151-b-pos the section table and the fact table again
  demanded that the answer "verify" a condition the case is silent about; the solver switched to
  the wrong answer; in rounds 2 and 3 grounded reasoning objected that "unproven is not false"
  and proposed a third answer the task does not allow. 0/2 (the other task was wrong from the
  solver's first answer on; the checks did not find the cause).
- **Format v2** (`check_report_v2/FORMAT.md`): the case text is read as complete. What it does
  not mention did not happen and does not exist for the task, so a condition that requires an
  absence is met when the case is silent; "the case does not say" is never a finding.
  `sc-smoke4-binary` (s151-b-pos, s152-b-1-neg, s2-b-1-a-ii-pos) ✅: 3/3, no finding of that kind;
  s151-b-pos keeps its correct answer through one unrelated finding.
- **Caveat** 🔶: this is a reading convention for the case description (a closed-world
  assumption), not a task-specific hint, but it is a convention of this kind of benchmark and
  goes beyond pure mechanics. It is in the check format only, not in the solver skill. Whether
  it stays is the owner's call (`04` §8). SARA's gold labels follow it (labels come from
  Prolog programs with negation as failure).
- The numeric run of §7.1 had already started with v1 when v2 was written and was left on v1,
  so that its 35 trials share one version.

## 7. Full runs under the cost protocol

Baselines of the same protocol and model (`09` §10.12, SARA numeric, one draw each): terminus-2
baseline 2/35 at 1.75 cents per task, solver-verifier 15/35 at 7.07, reader-verifier-compactor
17/35 at 6.51.

### 7.1 `sc-run1-numeric`: Qwen3.5-35B-A3B, SARA numeric (35 tasks, 2026-11-17) ✅

Code and skills of commit `21fac2e4` (check format v1), 8 concurrent trials, 04:59–11:53 UTC
(6 h 54 min; a two-task and a three-task smoke shared the server for about 3 h). 0 exceptions.

| Harness (same protocol, one draw) | Correct | Wilson 95% | Cents per task, no cache | with cache | p95 |
|---|---|---|---|---|---|
| terminus-2 baseline (`t2fix-cost1-numeric`) | 2/35 | 1.6%–18.6% | 1.75 | 1.54 | 3.75 |
| solver-verifier (`sv-cost1-numeric`) | 15/35 | 28.0%–59.1% | 7.07 | 4.99 | 12.20 |
| reader-verifier-compactor (`rvc-cost1-numeric`) | 17/35 | 33.0%–64.4% | 6.51 | 4.92 | 10.51 |
| **solver-checks (`sc-run1-numeric`)** | **19/35** | 38.2%–69.5% | **9.40** | 7.20 | 14.95 |

Cost detail in `outputs/sc_run1_numeric_inference_cost.json`. The intervals overlap widely:
19 vs 17 is not a significant difference on 35 tasks and one draw 🔶.

- **Where the gain comes from** ✅. First checked answer correct in 12/35; final answer correct
  in 19/35. The checks turned 8 wrong first answers into right ones (tasks 6, 44, 56, 67, 70,
  71, 74, 83) and one right answer into a wrong one (61). 15 trials were wrong before and after.
  So the solver skill with the rule document in context already reaches 12/35 before any check
  (baseline: 2/35), and the checks add 7.
- **Against `rvc-cost1-numeric`, task by task** ✅: newly correct 6, 15, 67, 74, 91, 97; newly
  wrong 49, 51, 61, 63. The 11 tasks no harness has solved (10, 18, 29, 35, 39, 47, 58, 60, 64,
  78, 94) are all still wrong; 8 of them finished `verified`, i.e. all three checks passed a
  wrong answer.
- **Rounds** ✅: 1 round in 7 trials, 2 in 10, 3 in 18. Finish reasons: `verified` 20 (11
  correct), rounds used up 14 (8 correct), stopped by the cost budget 1 (task 51). Round 1 passed
  in 7 trials (5 correct).
- **Checks** ✅: round-1 FAIL rate: section table 19/35, grounded reasoning 15/35, fact table
  13/35. Quotes: 156 found, 11 not found, 22 without a quote (computation findings of grounded
  reasoning). 38 repair requests, 90 fallbacks to no thinking in 235 check runs.
- **Rebuttals** ✅: 3 in 35 trials. 7 answer files were sent back by the format guard.
- **Cost** ✅: solver 3.89 cents, grounded reasoning 1.98, section table 1.94, fact table 1.59
  (59% of the cost is the checks). 94 replies were cut off at an output limit; a check run costs
  a median of 2.1k–2.4k completion tokens, but a run that reaches the 8,000-token cap pays for
  the thinking twice.

### 7.2 `sc-run1-binary`: Qwen3.5-35B-A3B, SARA binary (30 tasks, 2026-11-17) ✅

Same code, **check format v2** (§6.3), 8 concurrent trials, 11:53–14:56 UTC (3 h 3 min).
0 exceptions.

| | Correct | Wilson 95% | Cents per task, no cache | with cache |
|---|---|---|---|---|
| **solver-checks (`sc-run1-binary`)** | **23/30** | 59.1%–88.2% | 5.59 | 4.17 |

Cost detail in `outputs/sc_run1_binary_inference_cost.json`. **There is no baseline under the
cost protocol for SARA binary.** Under the old protocol (1,800 s wall clock, `06`, `08`) the
terminus-2 baseline had 26/30 and 27/30 and the solver-verifier harness 24/30 and 25/30; binary
trials rarely reached that time limit, so the numbers are roughly comparable 🔶.

- **The checks do not help on binary** ✅. First checked answer correct in 25/30, final answer
  in 23/30: no wrong answer was corrected, two right ones were lost, five were wrong before and
  after (all five passed all checks in their last round).
- **The two losses** ✅:
  - s152-d-2-h-pos: the answer stayed right throughout. The round-1 section table objected
    that two units were marked "does not apply"; the solver changed both lines. In round 2 the
    section table reached the output cap while thinking, and its reply without thinking
    **repeated the round-1 findings** ("the answer file claims §2 is not applied") although the
    file in its packet said "applies". The solver saw that the report did not match the file,
    concluded that the verifier was reading a cached copy, and tried to force a re-read instead
    of sending a rebuttal: seven verifier calls on a file whose content it had not changed
    (rewritten to the same text, an added empty line; no round used), then `# verified`
    appended to the answer line. Round 3 passed that file,
    the cost budget ended the trial, and the grader's exact match failed on
    `Entailment#verified`. Three harness-level weaknesses: a check without thinking can copy its
    earlier findings from the packet; the message for a call that runs nothing does not lead the
    solver to the rebuttal; no check and no guard looks at the form of the answer line.
  - s3306-c-1-pos: three rounds of section-table findings; the solver ended on the wrong answer
    with the argument that a status "is not established in the facts". The reading convention
    of format v2 is in the checks only; the solver has no such rule.
- **Round 1 passes more often than on numeric** ✅: 18/30 trials (15 correct). Section table is
  the check that fails most (10/30 in round 1) and the one that most often runs into the output
  cap (23 fallbacks in 48 runs).
- **Rebuttals**: 3.

### 7.3 `sc-run1-numeric` with Qwen3.8-27B (SARA numeric, 35 tasks, 2026-11-17/18) ✅

Same code and default skills as §7.1 (check format v1). Serving in `06` §2: 16 concurrent
trials, 15:20–06:07 UTC (14 h 47 min), 0 exceptions. The budget of 0.15 USD per task is computed
with the Qwen3.5 price card, so both models get the same token allowance; cost is reported with
that card and with the model's own price (`tools/pricing/price_card_qwen3.8-27b.json`).
`AGENT_TIMEOUT_MULT=96`: a first start with the default 4 h per trial was stopped after 16 min,
because at this speed a normal trial would have run into the time limit (its partial job dir is
kept as `sc-run1-numeric-aborted-agent-timeout`, not used). No baseline of this model exists.

| Solver-checks, SARA numeric (one draw each) | Correct | Wilson 95% | Cents per task, no cache | with cache | p95 | Model calls | Output tokens |
|---|---|---|---|---|---|---|---|
| Qwen3.5-35B-A3B (§7.1) | 19/35 | 38.2%–69.5% | 9.40 | 7.20 | 14.95 | 21.6 | 54,303 |
| **Qwen3.8-27B**, Qwen3.5 price card | **24/35** | 52.0%–81.4% | **7.99** | 6.73 | 17.02 | 14.9 | 55,819 |
| Qwen3.8-27B, its own price card | 24/35 | | 13.04 | 11.47 | 26.34 | | |

Cost detail in `outputs/sc_run1_numeric_qwen38_27b_inference_cost.json` (both models, Qwen3.5
card) and `outputs/sc_run1_numeric_qwen38_27b_inference_cost_own_card.json`. The intervals
overlap: 24 vs 19 is not a significant difference on 35 tasks and one draw 🔶.

- **The gain is in the solver, the checks changed no outcome** ✅. First checked answer correct
  in 24/35, final answer correct in 24/35: no wrong answer became right and no right answer
  became wrong. After a first report that did not pass (10 trials): 6 kept a correct answer, 2
  kept a wrong one, 2 changed a wrong answer into another wrong one (29, 51). With Qwen3.5 the
  same checks moved 12/35 to 19/35.
- **Against Qwen3.5, task by task** ✅: newly correct 2, 18, 49, 60, 63, 94; newly wrong 73.
  Wrong with both models: 10, 29, 35, 39, 47, 51, 58, 61, 64, 78. Tasks 18, 60 and 94 had not
  been solved by any harness with Qwen3.5.
- **Rounds** ✅: 1 round in 27 trials, 2 in 6, 3 in 2. Round 1 passed in 25 trials (18 correct).
  Finish reasons: `verified` 30 (21 correct), rounds used up 1 (task 60, correct), stopped by
  the cost budget 4 (29, 51, 83, 94; 83 and 94 correct, their answer files were already
  written). Of the 11 wrong answers 9 finished `verified`.
- **Checks** ✅: round-1 FAIL rate: section table 5/35, grounded reasoning 4/35, fact table
  2/35 (3 without a report). Quotes: 25 found, 0 not found, 8 without a quote. 4 rebuttals,
  1 repair request.
- **Most check runs ended without thinking** ✅: 89 of 125 check runs (71%) reached the
  8,000-token cap while thinking and were sent again with thinking off (Qwen3.5: 90 of 235,
  38%). The solver reached its 16,384-token limit in 24 replies (Qwen3.5: 1). In total 113
  replies were cut off, and they hold 52% of all output tokens (Qwen3.5: 94 replies, 38%).
  🔶 Whether the low FAIL rate comes from better answers or from checks that mostly ran without
  thinking is not separated by this run.
- **Cost** ✅: solver 3.82 cents, grounded reasoning 1.52, section table 1.45, fact table 1.21
  on the Qwen3.5 card (52% of the cost is the checks). Fewer calls than Qwen3.5 (14.9 vs 21.6)
  with the same output volume: 3,743 output tokens per call against 2,511.
- **Speed** ✅: 49 output tokens/s in total with 16 running requests, 3.1 per request; a single
  request gets 4.3 (`06` §2). Wall time per trial: mean 296 min, median 250, longest 631.

### 7.4 Prompt-based direct reasoning on the same price card (estimate, 2026-11-18) ✅

Question from the owner: what does direct reasoning (DeonticBench `generate_e2e.py --task
direct`: whole statute in the prompt, one call, no tools; run of 2026-10-16, `06`) cost under
the cost protocol? Estimated from the token counts stored in `source.json`, without new
generations, by `tools/db_direct_cost.py` (detail in `outputs/db_direct_qwen35_inference_cost.json`).
Qwen3.5-35B-A3B, thinking on.

| SARA | Accuracy, mean of 4 draws (exact match) | Prompt tokens | Output tokens per generation | Cents per task, one generation | p95 | All 4 generations, no cache / with cache |
|---|---|---|---|---|---|---|
| numeric (35) | 0.435, i.e. 15.2/35 [0.343, 0.543] | 6,848 | 12,057 | **1.30** | 1.59 | 5.21 / 5.02 |
| binary (30) | 0.823, i.e. 24.7/30 [0.767, 0.867] | 6,851 | 6,292 | **0.73** | 1.07 | 2.90 / 2.72 |

- **How it is computed.** One generation per task: prompt x input price + output x output
  price. A task has one call, so the with-cache cost equals the no-cache cost. Output is 93%
  (numeric) and 87% (binary) of the cost.
- **Limits of the estimate** 🔶. The script stored one output total per case for its four
  generations (one request with `n=4`), so "one generation" is the case mean, and the p95 is
  taken over case means, not over single generations. The run had the server's default cap of
  16,384 output tokens and no cost budget; 2 of 140 numeric generations came back empty.
- **Reading** 🔶. On numeric, one direct generation reaches about 15/35 at 1.30 cents; the
  solver-checks harness reaches 19/35 at 9.40 cents (7.2 times the cost), and the intervals
  overlap. A majority vote over the four direct generations scores 0.486 (17/35, `06`) at 5.21
  cents. On binary, direct reasoning is at 0.823 for 0.73 cents against 23/30 (0.767) for 5.59
  cents. The harness runs are one draw each, the direct figure is a mean of four.

### 7.5 Rough error attribution of `sc-run1-numeric` (2026-11-18) ✅

The 16 wrong final answers, each read against a derivation that reproduces the gold value
(offline analysis; gold values never reach a prompt). One primary error per task; 29, 39, 58,
60 and 94 carry a second one.

| Primary error | n | Tasks |
|---|---|---|
| Filing status: head of household assumed although the case does not say that the taxpayer maintains the household (35, 39, 61, 64, 94); §7703(b) not applied (78) | 6 | 35, 39, 61, 64, 78, 94 |
| Number of exemptions: the spouse's exemption denied on a joint return, from a literal reading of §151(b) (2, 29, 58, 60); a qualifying relative not counted (18, 49; also 39, 60) | 6 | 2, 18, 29, 49, 58, 60 |
| §3301 tax on wages paid: left out (47; second error in 29, 58, 94), applied where gold does not apply it (51) | 2 | 47, 51 |
| Other: additional standard deduction for age granted (63); one dollar from gold (10) | 2 | 10, 63 |

- **The errors are now judgments about status and persons, not arithmetic and not missing
  text.** Every answer file lists all nine sections, and the arithmetic of all 16 reproduces.
  12 of 16 are decided by who counts (filing status, spouse, dependents).
- **§3301 is looked at and dismissed.** In the four trials that omit its tax, the `## Sections`
  line reads "employer excise tax, not individual income tax": the walk-through forces a line
  per section, and the solver decides it from the heading. In `rvc-run1-numeric` (`09` §10.11)
  this omission decided 5 answers; here it decides 1.
- **The checks agree with the solver on these judgments.** 9 of the 16 finished `verified`
  (all three checks passed), 6 used up the rounds, 1 was stopped by the cost budget. Task 61
  was right at first and moved to head of household after the first report.
- 🔶 Five of the six filing-status errors rest on a fact the case does not state. This run used
  check format v1; whether v2 (the case text is complete, §6.3) removes them is untested.

## 8. Open points

- **Cost.** Checks cost more than the verifier they replace because three contexts each think
  about the same 9k-token prompt. Candidates, none tried: a lower thinking budget for the
  table checks, running the fact table only when the section table passes, skipping a second
  full round after a small correction.
- **Output cap of a check.** A check that reaches `check_max_output_tokens` (8,000) while
  thinking is sent again without thinking; the tokens of the first attempt are spent.
  With Qwen3.8-27B this happened in 71% of the check runs (§7.3), so the cap decides how the
  checks of that model work. Candidates, none tried: a higher cap for models that think longer,
  or a thinking budget set in the request.
- **What the checks add depends on the model.** They moved Qwen3.5 from 12/35 to 19/35 and
  changed no outcome for Qwen3.8-27B (24/35 before and after, §7.3), at 59% and 52% of the
  cost. A run of the solver skill alone (no verifier) per model would give the
  comparison directly; not run.
- **Rebuttals are rare.** The solver almost always edits the file after a finding
  (3 rebuttals in 35 numeric trials, 3 in 30 binary trials).
- **Three weaknesses seen in `sc-run1-binary`** (§7.2), none fixed yet: a check that falls back
  to no thinking can repeat its earlier findings from the packet instead of reading the new
  file; the reply to a verifier call that runs nothing does not point the solver to the
  rebuttal; the form of the answer line is not checked (a guard could require that nothing
  follows the value, which is benchmark-independent).
- **The reading convention of format v2 is one-sided.** The checks read the case as complete,
  the solver skill says nothing about it (§7.2, s3306-c-1-pos).
- **Numeric and binary ran on different check formats** (v1 and v2). The effect of v2 on
  numeric is not measured.
- **Plan B** (blind tables) and a **light context manager** are deferred. Revisit the context
  manager when a benchmark has a rule document above the inline limit or trajectories that
  approach the context window; revisit plan B if error analysis shows checks anchoring on the
  answer file's own selection.
- **Other benchmarks.** The code has no SARA-specific part; Airline and USCIS-AAO need a
  `rule_path` and possibly other `answer_headings`. Not run yet.
