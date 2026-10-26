# 10 · Rubric-based verifier sub-agent (terminus-2)

- **Date opened**: 2026-10-26
- **Purpose**: design and run log of the first self-verification module of the proposal
  ([05](05_confidence_proposal.md) §2.1): a **verifier sub-agent** that the solver must consult
  before it may finish, which checks the trajectory against five process rubrics in an
  **independent context**, with shell access to the same container, and reports back in a
  short fixed format. The solver then continues (repairs) or finishes with a confidence.
- **Code**: `tools/dar_terminus2_verifier.py` (`Terminus2RubricVerifier`, a subclass of the
  t2fix agent), `tools/skills/rubric_verifier/SKILL.md` (the verifier's instructions, hand-written
  v1), `tools/run_dar_qwen35_35b.sh` with `AGENT=t2verify`.
- **Confidence convention**: ✅ checked on the machine / in run output on the date given,
  🔶 expectation or inference.

## 1. Why a separate context, and why rubrics

Two findings from the native trajectories drive the design:

1. **In-context self-assessment is anchored.** The same model asked for its confidence inside
   its own trajectory reports a near-constant 0.86–0.97 whatever the answer; AUROC is at chance
   on SARA-Numeric ([08](08_verbalized_confidence.md) §2.1–2.4). The only elicitation with
   signal was the *answer-only* re-solve in a fresh context (AUROC 0.82–0.89, §2.2), i.e. the
   gain comes from **de-anchoring**. The verifier therefore runs in its own `Chat`, seeing the
   trajectory as data rather than as its own reasoning.
2. **The solver's own "verification" turn is empty.** Every native trajectory ends with a
   "let me verify my reasoning once more" turn that restates the same figures
   ([07](07_native_trajectories.md) §1.5, §1.6.1: KIRA's completion checklist produced 0
   corrections in 24 wrong trials). The dominant numeric errors are process failures that a
   checklist can name: statute text hidden by truncation and never retrieved (27/29 stock
   misses), amounts and section numbers from memory (16/29), an engaged provision dismissed as
   irrelevant (FUTA, 7/7/7 across runs), a condition treated as met without a supporting fact
   (head-of-household from "same abode", 7–8 per run), arithmetic in prose (python in 8/65
   trials). The five rubrics map onto these classes.

Design principle (owner, 2026-10-26): **the harness contains no checking logic.** It counts
rounds, builds the verifier's input, passes messages and logs. What is checked, how, and how
it is phrased lives entirely in `SKILL.md`, so the method is a natural-language verification
procedure that can later be optimised or transferred, not a benchmark-specific filter.

## 2. Protocol ✅ 2026-10-26 (code read against `terminus_2.py`)

Stock terminus-2 ends a trial in two steps: the first `task_complete: true` sets a *pending*
flag and sends back "Are you sure you want to mark the task as complete?"; the next
`task_complete: true` ends the trial, after executing any commands in that response. The
subclass keeps this protocol and changes only the confirmation message:

| Event | Stock terminus-2 | `Terminus2RubricVerifier` |
|---|---|---|
| First `task_complete` of an attempt | pending ← true; message "Are you sure?" | commands of that episode run; **verifier round r** runs (if r ≤ `max_verify_rounds`); message = rendered report + decision instruction |
| Reply without `task_complete` | pending ← false; loop continues | same (the solver "continues"); the next completion claim triggers round r + 1 |
| Reply with `task_complete` (pending) | commands run; trial ends | same; the top-level `"confidence"` field of that reply is recorded. If the reply also carried commands, the answer may have changed after the last verification; logged as `unverified_finish`, not prevented |
| Completion claim after the budget is spent | — | no verifier; message "No verification rounds remain … finish now with a confidence" |
| Trial ended via confirmation without a parsed confidence | — | one extra turn: "Confidence: NN%" |

Hooks: `_handle_llm_interaction` (records the claim and the decision), `_execute_commands`
(runs the verifier after the claiming episode's commands, so the answer file is already
written), `_get_completion_confirmation_message` (returns the report), `_run_agent_loop`
(the extra confidence turn and the summary file). The decision instruction is fixed text:

> To continue: address the items above with commands and do NOT set "task_complete". Change
> the answer file only if text you read from the statute or a computation you ran supports the
> change; your next completion claim will be verified again. To finish: respond with
> "task_complete": true, an empty "commands" array, and a top-level field "confidence":
> <integer 0-100>, your probability that the current answer is exactly correct.

After the final round the instruction says no further verification will run and asks for the
finish; a correction included in that same response runs and the trial ends (stock
semantics, made explicit).

## 3. What the verifier sees and returns

**Input (one user message)**: `SKILL.md` verbatim, then a packet built from the solver's
trajectory: the task instruction; the candidate answer file; every solver step as
`Analysis/Plan` text, the commands, and the observation **exactly as the solver saw it**
(10,000-byte limit and truthful truncation marker included); the current claiming episode;
the round number; on later rounds the previous report, the previous answer and whether the
answer changed. The solver's `reasoning_content` (thinking) is **excluded** by default
(`include_reasoning=false`, owner's decision 2026-10-26), matching the real conversation in
which thinking is not carried forward.

**Tools**: shell commands via `environment.exec` in the task container, non-interactive, up
to `verifier_max_steps = 8` command steps per round, each output capped at 6,000 bytes. The
skill tells it to locate with `grep -n`, read narrow ranges with `sed -n`, and compute with
`python3`. The solver's tmux pane is untouched.

**Output**: a JSON object per reply (`analysis`, `commands`, `report`); the round ends when
`report` is filled. The harness renders it as:

```
VERIFICATION REPORT (round 1 of 3)
1. Tool-call integrity: YES. …
2. Statute coverage: NO. lines 87–404 of 523 never appeared; §63(c)(2), §152, §2, §3301–3306 unseen.
3. Grounded reasoning: NO. the $6,350 standard deduction in step 3 does not occur in /app/statute.txt.
4. Provision selection and application: UNKNOWN. the agricultural-labour wages are not matched to any provision; §3301–3306 exist in the file.
5. Tool-assisted computation: NO. no python3 call; the 39.6 % bracket step was done in prose.
Unresolved:
- …
Suggested next actions:
- sed -n '380,420p' /app/statute.txt
```

Statuses are `YES / NO / UNKNOWN / NA` (the proposal's satisfied / violated / cannot be
assessed, plus not-applicable). Two rules in the skill shape the report: **no alternative
answer value** (the verifier points at the gap, never at the figure, so the measured effect is
that of verification, not of a second solve; cf. the answer-only re-solve of
[08](08_verbalized_confidence.md) §4, which is a different method), and **evidence or no
verdict** (`YES` needs a cited place, `NO` a cited violation, otherwise `UNKNOWN`).

## 4. SKILL.md v1: rubric order and content

Order in the skill: integrity → coverage → grounding → provision selection and application
→ computation. The first three are mechanical (scan the trajectory; `wc`/`grep` the file;
`grep` every figure and citation) and produce the leads for the fourth; the fourth is a
**fact ledger** (every case fact → consuming provision or `UNUSED`; a dismissed fact is a lead,
not a conclusion) plus a **condition ledger** (every applied provision → its conditions, each
with the fact that satisfies it; no inference from related facts); the fifth re-runs prose
arithmetic in python and reports mismatch without the corrected value. Wording is generic to
rule-grounded tasks (the file named in the instruction, "every tax the file makes X liable
for"); it was written after the failure analysis of [07](07_native_trajectories.md) §1.5.1 and
§1.6.1 on the 65 SARA `hard` tasks, so those tasks are development data for the skill
(decision in [05](05_confidence_proposal.md) §8).

## 5. Logs per trial

`<trial>/agent/rubric_verifier/round-N.json` (answer at the claim, parsed report, rendered
text, verifier commands and outputs, steps used, parse failures, tokens, seconds),
`round-N.packet.txt` (what the verifier saw), `trajectory.verifier-round-N.json` (ATIF, linked
from the solver's `trajectory.json` as a subagent reference), and `summary.json` (rounds,
decisions after each report with the parsed confidence, `final_confidence`,
`unverified_finish`, verifier token totals). Verifier tokens are added to the trial's
`n_input_tokens / n_output_tokens` through terminus-2's subagent metrics, so job totals
include them.

## 6. Settings for the first run (owner's decisions 2026-10-26)

| Setting | Value | Note |
|---|---|---|
| Base agent | t2fix (truthful truncation marker) | baseline `sara-t2fix-run1`, [07](07_native_trajectories.md) §1.4 |
| Model / sampling | Qwen3.5-35B-A3B on 0238, T 0.7, thinking on, 16k output per call | same server and settings as the baseline; the verifier uses the same `Chat` kwargs |
| `max_verify_rounds` | 3 | then the solver must finish with a confidence |
| `verifier_max_steps` | 8 | per round |
| `include_reasoning` | false | |
| Agent time limit | 600 s × 3 = 1,800 s wall clock, unchanged | verifier time counts against it; trials that exceed it are scored wrong as in [07](07_native_trajectories.md). Token budget remains the long-term definition |
| Controls | none new | compared against the existing `sara-t2fix-run1` baseline only |
| Skill | hand-written v1, not optimised | optimisation on a dev subset is a later step |

## 7. Runs

### 7.1 Smoke test (`jobs/qwen3.5-35b-a3b/t2verify-direct/verify-smoke1`, 2026-10-26) ✅

One SARA-numeric task (tax-case with "Bob is Alice's father, no income"; gold 102150). The
pipeline ran end to end with no exception: 2 verifier rounds, one repair turn, the solver
then finished with `task_complete` plus a `cat` of the answer file and no `confidence`
field, so the extra confidence turn fired ("Confidence: 100%"). Agent time 934 s (baseline
median for numeric 687 s), of which the verifier took 485 s; verifier tokens 66k prompt +
14k completion on top of the solver's own. Files written as described in §5; the verifier
rounds appear in `trajectory.json` as system steps with subagent references (first version
reused terminus-2's summarisation hook, whose fixed message reads "Performed context
summarization and handoff"; replaced by an explicit "Rubric verifier round N consulted"
step before the full run).

What the verifier did (round 1, 5 command steps, 19 commands, 204 s): `wc -l`, listed the
section headings, grepped the solver's cited §161 and §160 (absent), read §63 and §151, re-ran
the solver's two arithmetic steps in python. Report: integrity YES, coverage YES, grounding
**NO** (§161 does not exist; the standard deduction is in §63), provision selection YES,
computation **NO** (prose arithmetic). Suggested actions: verify citations, use python3,
"replace §161 with §63". Round 2 (0 commands, 282 s of thinking): same five statuses, one
unresolved item (the §161 citation in earlier steps).

What the solver did: after round 1 it re-ran its own arithmetic in python, confirmed 102546,
and re-claimed; after round 2 it argued that the citation error is "in my reasoning text, not
in the final answer" and finished. The answer never changed and is wrong: the solver treated
Alice as single with one exemption; the case facts make Bob a dependent (§152) with the
consequences the gold Prolog applies. Neither round touched §152 or the fact "Bob has no
income": provision selection was marked YES both times on the basis of the provisions the
solver *did* apply, i.e. the fact ledger of the skill was not actually executed. Two
observations for the skill, recorded here and not acted on before the full run (hand-written
v1 is frozen for `sara-t2verify-run1`):

1. The verifier re-reports historical defects that cannot be remedied (a citation in an
   earlier step) as unresolved, which drives repair rounds that cannot change the answer.
2. The fact ledger needs to be forced (one line per fact, every fact), otherwise the verifier
   checks the solver's provisions instead of the case's facts and the FUTA / dependency /
   filing-status classes of [07](07_native_trajectories.md) §1.5.1 pass unnoticed.

### 7.2 Full SARA pass (`sara-t2verify-run1`, started 2026-10-26 01:05 UTC)

65 tasks (sara-numeric 35 + sara-binary 30), `--n-concurrent 4`, settings of §6. A first
launch at 00:49 was discarded after two trials (kept as `broken-run0-step-id-bug`): the
system step added after the smoke test was built with `step_id=0`, pydantic rejected it after
the report had been produced, and the solver received the error fallback instead of the
report. Fixed (`fc50340`), verified on the first trial of the restart (report present in the
solver's observation, system step recorded). Finished 05:42 UTC (4 h 37 min wall); 65/65
trials, 11 `AgentTimeoutError` (all numeric). Scored with `tools/verifier_job_summary.py
<job> --baseline jobs/qwen3.5-35b-a3b/t2fix-direct/sara-t2fix-run1 --trials` ✅ 2026-10-26.

**Grading convention.** Harbor still runs `test.sh` on a timed-out trial, so a timed-out
trial has a reward from whatever was in the answer file. The protocol of
[07](07_native_trajectories.md) counts timed-out trials as wrong; the table gives that
number first and the file-graded number in brackets. The baseline's single timeout
(case 94) was wrong by file as well, so its numbers are the same under both conventions.

#### Accuracy, per task against the baseline (same 65 tasks, one draw each)

| Subdomain | n | run-1 correct | baseline `sara-t2fix-run1` | both | gained | lost | of lost: by timeout |
|---|---|---|---|---|---|---|---|
| sara-numeric | 35 | **10 (0.286)** [file-graded 13 (0.371)] | 7 (0.200) | 5 | 5 | 2 | 2 (cases 85, 71: correct answer in the file, no time left to confirm) |
| sara-binary | 30 | **24 (0.800)**, macro-F1 0.796 | 27 (0.900) | 24 | 0 | 3 | 0 |

Accuracy at DAR's 600 s limit: numeric 0/35, binary 21/30; every numeric trial now exceeds
600 s, so the paper-comparable column is empty for this agent.

#### What the verifier changed within a trial (finished trials only)

| Subdomain | n finished | acc at first completion claim | final acc | answer changed | wrong→right | right→wrong |
|---|---|---|---|---|---|---|
| sara-numeric | 24 | 5/24 (0.208) | 10/24 (0.417) | 16 | 6 (cases 15, 51, 63, 73, 74, 93) | 1 (case 56) |
| sara-binary | 30 | 25/30 (0.833) | 24/30 (0.800) | 3 | 1 (xC7p9y8) | 2 (JBnGGhw, akzbtTQ) |

Rounds: numeric 2 rounds in 21 trials, 3 in 3; binary 1 round in 21, 2 in 8, 3 in 1. The
solver chose *continue* after 27 of 51 numeric reports and 10 of 40 binary reports, never
finished with commands that changed the answer after the last round, and needed the extra
confidence turn twice. The verifier always returned a report (no step-budget or context
failures); mean 3.3 command steps per numeric round, 2.2 per binary round; 0 commands in 6
rounds (round-2 confirmations).

**All six numeric repairs are the standard-deduction class** of
[07](07_native_trajectories.md) §1.5.1: the solver had used $4,400 / $6,350 / $12,700 with a
fabricated §162 / §163 / §161 citation; the verifier `grep`'d the citation (absent), read
§63(c), reported "the figure is not in the file" and pointed at the section; the solver re-read
it and recomputed, mostly with python. The two baseline-wrong cases among them (15, 63) are
the net gain from repair; the other four were right in the baseline draw, so on those tasks
the verifier recovered what sampling had lost. Case 63 took all three rounds (5 → 4 → 3 items
not YES) and 1,711 s.

**What was not repaired** (14 finished numeric trials still wrong): the classes the
verifier cannot see in the solver's text. Dependents (cases 18, 29, 49, 60), FUTA
(47, 58, 91, 97), filing status (35, 39, 64, 78), the 2018 rule (90), §68 / phase-out (6, 56).
Round-1 reports mention the standard deduction in 24 of 28 numeric reports, a non-existent
section in 11, dependents in 11, filing status in 7, **FUTA in 3** (of 6 FUTA cases that
reached a round). Seven of the 14 changed their answer after the report and stayed wrong
(e.g. 60: 3641 → 4631, gold 4331; 47: 43204 → 45616, gold 45946): the verifier's lead fixed
one item and the remaining item kept the answer off.

**The right→wrong cases are the verifier's own errors, obeyed by the solver.**
Numeric case 56 (AGI $395k, itemised deductions): the verifier reported "§151 personal
exemption of $2,000 not applied" and suggested adding it; the exemption is phased out to
zero at that AGI by §151(d)(3), the solver's first answer (130388) was right, the repaired
one (129596) is wrong. Binary JBnGGhw and akzbtTQ: the verifier flagged "a condition of the
cited section is not stated in the facts" and the solver flipped Entailment →
Contradiction; the statement presupposes the section, so the flip is wrong. In all three
the report contained a replacement figure or conclusion, against the skill's rule.

#### Confidence and rubric signals

| Subdomain | n | stated confidence: mean / distinct values | AUROC | ECE | Brier | rubric score (1 − notYES/5), last round: AUROC | round 1: AUROC |
|---|---|---|---|---|---|---|---|
| sara-numeric | 24 | 1.00 / {1.0} | 0.50 | 0.58 | 0.58 | 0.43 (21 of 24 trials all-YES) | 0.60 |
| sara-binary | 30 | 0.99 / {0.9, 0.95, 1.0} | 0.51 | 0.19 | 0.20 | 0.46 (28 of 30 all-YES) | 0.61 |

The solver's stated confidence after the verifier loop is exactly as constant as in
[08](08_verbalized_confidence.md): every numeric trial says 100 %. The last-round rubric
statuses are almost all YES by construction (the solver repairs until the verifier is
satisfied), so they carry no signal; the round-1 count of items not YES is weakly ordered
(AUROC 0.60 / 0.61 with 10 / 6 positives, CIs include 0.5). Neither reaches the working target
of [05](05_confidence_proposal.md) §8 (AUROC ≥ 0.7 and ECE ≤ 0.15).

#### Cost

| Subdomain | solver-only time, median | agent time incl. verifier, median (baseline) | verifier time / trial, mean | verifier tokens / trial, mean | solver tokens / trial, mean (baseline completion) | trials > 1,800 s |
|---|---|---|---|---|---|---|
| sara-numeric | 677 s | 1,119 s (687 s) | 471 s | 101k prompt + 7.6k completion | 69k prompt + 11.5k completion (11.5k) | 11 (baseline 1) |
| sara-binary | 232 s | 442 s (210 s) | 254 s | 40k prompt + 4.0k completion | 34k + 4.1k (3.9k) | 0 |

Verifier prompt tokens are the sum over the 4–6 calls of a round, each of which re-sends the
whole verifier conversation (skill ≈ 2.5k + packet 5–9k + tool outputs; single calls are 9–15k
tokens). The vLLM server reports a **prefix-cache hit rate of 0.0 %** throughout: prefix
caching is disabled by default for this hybrid Gated-DeltaNet model, so every call re-prefills
from scratch, for the solver's own episodes as much as for the verifier. With four trials
sharing the server, that prefill load is what slows the other solvers: in the timed-out trial
`rVqkyP7` one 7,843-token solver generation took 1,654 s (4.7 tok/s against ~15 tok/s normal).
Of the 11 timeouts, 5 occurred before the first completion claim (0 verifier rounds; the
solver's own generation stalled), 6 during repair after one round.

#### Reading

1. **Verification → repair works on the error class that is visible in the solver's text.**
   Six of 24 finished numeric trials went from wrong to right, all standard-deduction /
   fabricated-citation cases; none of the dependents, FUTA, filing-status or phase-out
   cases was repaired, because the solver's analysis never mentions the fact or section the
   verifier would need to question (FUTA named in 3 of 28 reports). This is the argument for
   making the solver state its derivation explicitly (§8).
2. **The 1,800 s wall clock takes back most of the gain.** Net +3 numeric under the protocol,
   +6 file-graded; 11 timeouts against 1 in the baseline, three of them with the right answer
   already in the file. The cause is server contention from un-cached prefill, not the
   verifier's own reasoning time (median 446 s per numeric trial).
3. **The verifier's reports are not answer-free.** 16 of 52 round-1 reports contain a figure
   or a python expression; the three right→wrong cases are reports whose replacement was
   wrong and which the solver followed without re-deriving. Binary, at 0.83–0.90 saturation,
   has only downside: 1 repair, 2 damages.
4. **No confidence signal yet.** Stated confidence is constant; last-round rubric statuses are
   saturated by the repair loop itself. Any rubric-derived confidence has to come from the
   round-1 report, from unresolved items, or from the solver's derivation record, not from
   the final statuses.

Raw per-trial records: `outputs/verifier_run1_trials.json` and
`outputs/verifier_run1_summary.txt` on 0238 (gitignored); the numbers above are the shared
copy.

## 8. Run-2: derivation record, index packet, skill v2 (owner's decisions 2026-10-26)

Decided after the review of §7.2. Everything below is switchable by agent kwargs so that
run-1 stays reproducible (`AGENT=t2verify2` in `tools/run_dar_qwen35_35b.sh` sets them all).

| Change | Mechanism | Why |
|---|---|---|
| **Solver writes a three-section answer file** (`derivation_record=true`) | The solver's prompt template is swapped for `tools/templates/terminus-json-plain-derivation.txt`: the upstream `terminus-json-plain.txt` plus one paragraph after the task description. The answer file named by the task must contain `## Statute` (verbatim quotes with section and line numbers), `## Reasoning` (provisions applied to the facts, origin of every number, python for arithmetic) and `## Answer`, with `Answer: <value>` as its last line. DAR's `test.sh` takes the last `Answer:` line, so the grader is unchanged. The solver writes it the way it already writes the answer (a heredoc through the terminal); no new mechanism. | §7.2 reading 1: the verifier only catches what is visible in the solver's text. The record makes the provisions and numbers explicit, so coverage and grounding become line-level checks and the fact ledger becomes a diff between the instruction and the Reasoning section. The idea is DAR's unreleased `kira_deontic_grounded`. The solver is **not** told what the verifier checks; the report says it. |
| **No confidence** (`report_confidence=false`) | Finish instruction asks only for `task_complete: true` with empty commands; no extra turn. | Owner: a constant 100 % after an all-YES report carries nothing (§7.2 confidence table). |
| **Index packet** (`packet_mode=index`) | The verifier's first message holds the instruction, the answer file and a one-line-per-step index (commands, output size, truncation marker, completion claim). The full trajectory text is uploaded to `/tmp/rubric_verifier/trajectory.txt` in the container (`environment.upload_file`); the verifier reads it with `grep -n` / `sed -n` like the statute. | Owner: let the verifier use the harness's own capabilities instead of receiving everything in-prompt. Also cuts the per-call prompt from 9–15k to a few k tokens. |
| **Skill v2** (`tools/skills/rubric_verifier_v2/SKILL.md`) | Checks target the record: statute quotes verified at the cited lines and against what the solver's terminal actually displayed; every number in Reasoning classified (fact / quote / computation); fact ledger is a required report field `facts` (every case fact → consuming section or `UNUSED`); condition check includes limitations the file applies on top of the quoted provisions; entailment questions do not fail on facts the statement presupposes; "point, do not solve" rule with `quoted_lines`; only actionable items in `unresolved`; later rounds are delta checks. | §7.2: the three right→wrong cases were reports that supplied a wrong replacement; FUTA / dependents / status were never questioned because the fact ledger was advisory and not executed; round-2 reports re-audited everything. |
| **Server: prefix caching on** | `tools/serve_qwen35_35b.sh` now passes `--enable-prefix-caching` (`PREFIX_CACHE=0` restores the old configuration). vLLM 0.20.1 accepts it for this model with "Mamba cache mode 'align'" and marks it experimental. | §7.2 cost: 0 % hit rate made every call re-prefill; verifier prefill starved the other solvers and caused the timeouts. Baseline is not re-run (owner): its single timeout was wrong by file anyway. |
| Unchanged | t2fix base, T 0.7, thinking on, 3 rounds, 8 verifier steps, no solver thinking in the packet, 1,800 s wall clock, 65 SARA tasks, 4 concurrent, no new controls; binary included for comparability. | |

Job: `jobs/qwen3.5-35b-a3b/t2verify-direct/sara-t2verify-run2`. Smoke test on one numeric
and one binary task first; after the smoke test no code or skill change before the full run.
If prefix caching could not be enabled, the contention problem has to be solved another way
before run-2 (owner's instruction); the §8 run log records which.

### 8.1 Run log

(filled in as runs complete)
