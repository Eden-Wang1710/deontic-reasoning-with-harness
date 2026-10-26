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

(filled in as runs complete)
