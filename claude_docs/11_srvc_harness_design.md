# 11 · Solver-reader-verifier-compaction harness: design of v0.1

**Status (2026-11-20): implemented as `AGENT=t2srvc`** (`tools/dar_terminus2_srvc.py`; §9
describes the implementation and where it differs from this design), **run in full on SARA
numeric and binary (§10) and put on KIRA's loop as `AGENT=kirasrvc` (§11; full SARA runs in §11.1)**. §1 records what the owner
has decided (2026-11-19 the frame, 2026-11-20 the verifier, the window, the cost column). The
owner confirmed all six points of §8 on 2026-11-20 and asked for SARA numeric and binary in
full, then the KIRA adapter. The numbers in §2 are measured on finished runs with
`tools/sc_check_stats.py`.

**Names of the two versions (owner, 2026-11-20).** The harness on the terminus-2 loop is the
**solver-reader-verifier-compaction terminus-2-harness v0.1** (`AGENT=t2srvc`); the harness on
KIRA's loop is the **solver-reader-verifier-compaction terminus-kira-harness v0.1**
(`AGENT=kirasrvc`). Where this document says "the harness" or "v0.1" without a base agent it
means the architecture common to both. The identifiers in code and logs are unchanged
(`t2srvc`, `kirasrvc`, and `"harness": "srvc-v0.1"` in a trial's summary, which
`tools/sc_run_report.py` reads).

**Naming.** From v0.1 on a check of the verifier is called a **rubric**, and the table it fills
is its **rubric table** (owner, 2026-11-20). §2 keeps the word *check* because it describes the
runs of the solver-checks harness.

The harness that exists today is the solver-checks harness (`10`, `AGENT=t2sc`): a solver with the
rule document in its first message and a verifier made of three independent checks. v0.1 is the
complete harness: solver, reader, verifier and compaction.

## 1. Decisions that fix the frame (owner, 2026-11-19)

- The complete harness has four roles: **solver, reader, verifier, compaction**. The role that
  manages the context is called *compaction*, not *compactor*.
- **Reader and compaction are built on the solver's context**: the request is the solver's
  messages so far plus one appended user message, the way Claude Code and Codex compact. The
  prefix is then read from the prompt cache. A new context with its own system prompt cannot do that.
- **The verifier stays independent** of the solver's context.
- The reader stays in the harness although no DAR rule document needs one for its size (`03`: SARA
  6,097 tokens, Airline 3,597, USCIS under 1,000). Its job is to keep the solver's context clean.
  The benchmark where this matters is BrowseComp-Plus, which is planned next.
- Compaction is worth studying at our context sizes. The CLM work on BrowseComp-Plus forces a
  32K harness window on a model with a 256K native window, against an average task context of
  about 97K, to study compaction. Tens of thousands of tokens with much repetition is a fair
  setting for it.
- The harness is to be carried over to KIRA after terminus-2.
- The cost of the verifier is to come down (§4).

Added on 2026-11-20:

- **Thinking of a rubric is never thrown away** (§4.1).
- **The verifier runs one round per task** (§4.2).
- **All three rubrics stay**: section table, fact table, grounded reasoning (§4.3).
- **Compaction: a 32K window, and the solver may call it itself** (§3.3).
- **The price with cache is the headline cost** (§5).

## 2. What the finished runs say (2026-11-19) ✅

`tools/sc_check_stats.py <job dir>`, on `sc-run1-numeric` with Qwen3.5-35B-A3B unless a column
says otherwise. "Right" compares the answer line of the checked file with the expected value
(offline analysis only).

**Where the money goes** (cents per task, `10` §7.1): no cache 9.40 = solver 3.89 + checks 5.51;
with cache 7.20 = solver 2.54 + checks 4.66. Output tokens are 5.43 of the 7.20. The checks
produce 73% of all output tokens.

| Measure | Qwen3.5 numeric | Qwen3.8-27B numeric | Qwen3.5 binary |
|---|---|---|---|
| Check runs: finished with thinking / cut at 8,000 and sent again without thinking | 145 / 90 | 36 / 89 | 108 / 33 |
| Output tokens thrown away at the cap | 720,000 (2.06 cents per task) | 712,000 | 264,000 |
| Check output tokens in round 1 / 2 / 3 | 622k / 518k / 247k | 864k / 142k / 15k | 432k / 189k / 75k |
| Thinking replies that finished: p50 / p75 / p90 tokens | 2,304 / 3,497 / 6,760 | 5,405 / 6,546 / 7,214 | 2,715 / 5,643 / 6,739 |
| Share of those within 4,000 tokens | 79% | 38% | 59% |
| Round 1, answer file right: report not passed | 7 of 12 | 6 of 24 | 10 of 25 |
| Round 1, answer file wrong: report not passed | 21 of 23 | 4 of 11 | 2 of 5 |
| Solver context, largest prompt: mean / p90 / max tokens | 22,980 / 30,219 / 31,879 | 16,675 / 19,297 / 25,323 | not computed |

**Round 1 does the work; rounds 2 and 3 mostly cost.** In the Qwen3.5 numeric run, 8 answers went
from wrong to right and 1 from right to wrong (12/35 before the checks, 19/35 at the end). All 8
corrections were made after the round-1 report. None of the 13 answers that were still wrong in
round 2 ended right. Rounds 2 and 3 hold 55% of the check output (2.19 cents per task), and on
answer files that were already right their report did not pass in 15 of 21 cases (8 of 13 in
round 2, 7 of 8 in round 3).

**The three checks do not separate right from wrong equally.** FAIL in round 1, over the three runs:

| Check | Right answer files (61) | Wrong answer files (39) |
|---|---|---|
| Section table | 19 (31%) | 15 (38%) |
| Fact table | 4 (7%) | 14 (36%) |
| Grounded reasoning | 6 (10%) | 15 (38%) |

The section table fails a right file about as often as a wrong one. It is also the check that
makes a right file fail most often (7 of the 7 not-passed right files in Qwen3.5 numeric round 1
carry a section-table FAIL, 9 of 10 in binary). 🔶 A FAIL on a wrong file is not proof that the
finding concerns the actual error, and a FAIL on a right file can be a true defect of the
`## Sections` list that does not change the answer. The counts say what a FAIL is worth as a
signal, not whether each finding is true.

**Reports written without thinking carry less signal.** Qwen3.5 numeric, all rounds: a report
made with thinking fails 28% of right files (16 of 57) and 42% of wrong ones (37 of 88). A report
made after the cap, without thinking, fails 48% of right files (19 of 40) and 42% of wrong ones
(21 of 50). 🔶 The runs that hit the cap may be the harder cases, so this is not a clean comparison.

**The solver's context is repetitive.** Last solver prompt of a trial, Qwen3.5 numeric: first
message (task, rule document, skill) 47% of the characters, terminal output 27%, the solver's own
replies 19%, verifier reports 7%. The answer file is written 2.6 times per trial and shown again
in terminal output 4.6 times, so about 7 copies of it are in the context. No trial reaches 32,000
tokens; 30 of 35 pass 16,000 and 24 pass 20,000.

## 3. Architecture

    main line (solver):   [first message] [turn 1] [turn 2] ... [turn n]
                                                                   |
    branch (reader or compaction):   same messages, byte for byte  + [role message] + branch turns
                                                                   |
    back on the main line:                                         + [result message built by the harness]

    verifier:   [task + source + report format] [check skill] [packet]      one context per check

### 3.1 Branches

A branch is a request whose messages are the main line as it stands plus one appended user
message. That message suspends the solver's reply format, states the role and its own reply
format, and carries the role's skill. Replies and tool output of the branch stay in the branch.
When it ends, the harness builds one result message and appends it to the main line as the
solver's next observation. The main line never contains the branch's work, only its result.

- **Cache.** The branch prefix is the main line, so it is billed as a cache read. The main line
  keeps its own cache, because nothing in it was rewritten.
- **Knowledge.** The branch knows the case and everything the solver has done. A query can be one
  line.
- **Ledger.** Branch calls are logged under their role (`reader`, `compaction`), as today.
- **Risk.** The branch inherits the solver's system prompt and may go on solving. The harness
  validates the shape of the branch reply and asks once for a repair, as it does for the checks.

### 3.2 Reader

- **Calls.** The harness makes one *survey* call before the solver's first step when the source
  is not printed in the first message. Later calls come from the solver with a query
  (`"subagent": {"name": "reader", "query": "..."}`).
- **Access to the source.** A source up to `reader_inline_max_chars` is printed with line numbers
  in the branch message. A larger one is read with tools inside the branch (grep and sed on a
  file; a search tool on BrowseComp-Plus), up to `reader_max_steps`.
- **Reply.** Pointers, never text: an outline (survey call only: one line per top-level unit with
  its line range and whether the case engages it) and line ranges with a label and a note. The
  harness copies the lines verbatim, drops ranges it has already delivered and applies the
  character limit.
- **Result on the main line.** The outline and the verbatim excerpts. The solver can read any
  range of the outline directly with a shell command, without a model call.
- **Known risk.** In `rvc-run1-numeric` about 9 wrong answers came from provisions the reader
  never delivered (`09` §10.11). Two things answer it in v0.1: the outline lists every unit, so
  an omission is visible and can be fetched; and the verifier works from the full source in its
  own context (§3.5), so a missing provision can be found by an agent that did not depend on the
  reader.
- **SARA.** The rule document is 6,097 tokens, so the reader adds calls without relieving the
  context much. Its case on DAR has to be measured (`rule_inline_max_chars=0` forces it).

### 3.3 Compaction

- **Trigger.** The harness, when the next solver prompt would pass `context_window` tokens
  (32,000 in v0.1). The solver may also call it (`"subagent": {"name": "compaction"}`); the
  solver skill says when that is worth it (the context holds work that is finished and no longer
  needed word for word). No call is made otherwise, so an unused compaction costs nothing.
- **Step 1, branch call.** The compaction skill asks for a state page: what is established, what
  is open, which decisions were taken and why. Sources are named by line range and earlier work
  by step number; rule text and the answer file are not restated. Output limit
  `compaction_max_output_tokens` (1,500).
- **Step 2, harness, no model.** The main line is rebuilt as: the first message unchanged (still
  a cache read), then one message with the state page and the pinned items read from where they
  live (source lines for the ranges the state page names, the answer file from disk, the latest
  verifier report, rounds and budget left), then the last `keep_last_turns` turns verbatim.
- **Guards.** A state page that does not parse or passes its limit is rejected. A rebuild that is
  not clearly smaller than what it replaces is dropped and the old context kept.
- **Cost of one compaction** at a 16K window: 16,000 tokens read from cache (0.08 cents) plus
  1,500 output tokens (0.15 cents), plus one prompt after it whose new part is not cached (about
  0.05 cents). About 0.3 cents.
- **What it buys.** Under cache prices a shorter prompt saves little (10,000 tokens less is 0.05
  cents per later call), so compaction about pays for itself and no more. Its case is the window
  and, possibly, accuracy from a cleaner context. 🔶 The second is unproven: compactor v1 gave no
  gain (`09` §10.10).
- **SARA.** The 32K window does not fire there (largest prompt 31,879 tokens with the rule
  document inline, and one verifier round makes the context shorter). On SARA compaction
  therefore happens only when the solver calls it, which is what v0.1 tries out. A 16K window
  would fire in most trials and remains the setting for a forced study.

### 3.4 The answer file in the context

In the Qwen3.5 numeric run the solver's last prompt holds about 7 copies of the answer file per
trial: 2.7 in the solver's own replies (the heredoc that writes it), 2.5 as the terminal's echo
of that heredoc, 2.5 as later displays (a `cat`, or the screen still showing it). Together that
is roughly 19,000 of 80,000 characters. v0.1 removes the copies it can:

- **Writes go through the harness, not the terminal.** The solver's reply gets a field
  `"write_file": {"path": "...", "content": "..."}` (a tool in the KIRA adapter). The harness
  writes the file and answers with one line (path, number of lines, the last line). The terminal
  never echoes the content, and there is nothing to check with `cat`.
- **One verifier round** leaves at most two writes: the first file and one correction.
- **The verifier's result message** does not print the file back (as today).
- **Compaction** keeps one copy, read from disk.

What stays is one copy per write in the solver's own reply. 🔶 A new reply field is one more
thing the model can misplace (the `subagent` field needed a guard for that, `09` §5).

### 3.5 Verifier

Independent contexts, as today: each rubric sees the task, the source, the report format, its
own skill and the packet (the answer file). It never sees the solver's reasoning. The prefix
(task, source, format) is the same for every rubric, so it is a cache read after the first call.

The verifier keeps the full source even when the solver works from reader excerpts. Where the
source is too large for that (BrowseComp-Plus), a rubric reads it through its own tool steps.

## 4. The verifier in v0.1 (owner, 2026-11-20)

### 4.1 Thinking is kept

A rubric call runs with thinking and an output limit (`rubric_max_output_tokens`, 8,000 as
today). When the reply ends inside its thinking, the second call gets that thinking as "your
notes so far" in an appended message and writes the report without thinking again. Nothing that
was paid for is discarded, and no report is written from nothing. The second call reads the
notes as input (8,000 tokens, about 0.11 cents at the uncached price).

The limit is a separate setting. 4,000 would cover 79% of Qwen3.5's finished thinking replies
and 38% of Qwen3.8's (§2); v0.1 keeps 8,000 so that the only change is that thinking is kept.

### 4.2 One round

- The solver calls the verifier once, on an answer file with the required sections. Every rubric
  runs once.
- The report goes to the solver. For each finding it reads the quoted text and either corrects
  the file or leaves it. Then it finishes. The corrected file is not checked again.
- `task_complete` is accepted once the verifier has run. A second call is answered with the
  report that exists, as today for an unchanged file.
- The rebuttal exchange and the round budget are gone, with their text in the solver skill and
  the "later rounds" part of the report format.

✅ Looking back at the Qwen3.5 numeric run: taking each trial's answer file after its first
correction gives 18/35, against 19/35 with three rounds (paths over the rounds, right R and wrong
W: R 5, RR 3, RRR 2, RWR 1, RWW 1, W 2, WR 2, WRR 5, WRW 1, WW 5, WWW 8). Round 1 holds 622k of
the 1,387k check output tokens, so the rubric output per task falls from 3.96 to about 1.78
cents. 🔶 This assumes the solver corrects as it did when a second round was coming.

### 4.3 Three rubrics

Section table, fact table and grounded reasoning stay, each with its rubric table. The section
table fails right and wrong files at a similar rate (§2), and with one round a FAIL no longer
costs another round; what it costs is a correction the solver may make to a right answer (1 of
the 7 right files that did not pass round 1 ended wrong). The solver skill keeps the rule that a
finding is a claim to be read against the quoted text, not an order.

Skill directories are renamed from `check_*` to `rubric_*` in the v0.1 skills
(`rubric_section_table_v1`, `rubric_fact_table_v1`, `rubric_grounded_reasoning_v1`,
`rubric_report_v3`); the `check_*` directories stay for `AGENT=t2sc`.

### 4.4 Rough cost 🔶

Estimate from the Qwen3.5 numeric run, not a result: rubric cost with cache falls from 4.66 to
about 2.2 cents per task, so about 4.7 cents per task with cache before the reader and
compaction are added. The old separate reader cost 1.43 cents per task with cache at 12.6 calls
per task; one compaction costs about 0.3 cents.

## 5. Cost columns

Branches resend the solver's context on every call. With cache that is cheap by design; without
cache it is the most expensive way to call a sub-agent. **The price with cache is the headline
cost from v0.1 on (owner, 2026-11-20); the no-cache price stays in the table.** 🔶 To confirm:
the budget per task (0.15 USD, `09` §10.12) is computed at the price with cache as well.

## 6. What is harness and what is skill

- **Harness (no benchmark-specific code):** main line and branches, the verifier's contexts,
  shape validation, the quote search, the completion gate, the file-write channel, the rebuild
  after compaction, the ledger and the budget.
- **Skills (per benchmark):** solver, reader, compaction, each rubric, the report format.
- **Source adapter (per benchmark):** how a piece of the source is addressed and copied. File and
  line range for DAR; document and passage for BrowseComp-Plus.
- **Agent adapter:** terminus-2 calls sub-agents through the `subagent` field of its JSON reply.
  KIRA uses native tool calling, so `reader`, `verifier`, `compaction` and the file write become
  tools. The core is written once and the two adapters stay thin.

## 7. Order of work

1. Core and terminus-2 adapter (`AGENT=t2srvc`), skills `solver_v5`, `reader_v2`,
   `compaction_v1`, the three `rubric_*_v1` and `rubric_report_v3`.
2. Smoke runs, then SARA numeric and binary with Qwen3.5-35B-A3B. This gives the row
   "solver-reader-verifier-compaction" of the paper table; the 19/35 at 9.40 cents printed there
   now comes from a run in which the reader was never called.
3. KIRA adapter. Then BrowseComp-Plus.

## 8. Confirmed before the implementation (owner, 2026-11-20) ✅

The owner confirmed every point below on 2026-11-20 ("可以，先把 SARA numeric 和 binary 全量，
再接 KIRA").

- **The rule document leaves the solver's first message on SARA** and comes through the reader
  (outline plus excerpts). This is what "with a reader" means on a 6,097-token document, and it
  is the main accuracy risk of v0.1 (§3.2). The verifier keeps the complete text.
- **Naming**: check becomes rubric, its table a rubric table; the skill directories are
  `rubric_*`.
- **The file-write channel** of §3.4 (`"write_file"`) as the answer to the repeated answer file.
- **Rubric output limit** stays 8,000 (§4.1).
- **Budget** of 0.15 USD per task at the price with cache (§5).
- **Thinking in the branches**: the reader thinks, with the same keep-the-thinking rule as the
  rubrics; compaction writes the state page without thinking.

Still open from `10` §8 🔶: report format v2 (the case text read as complete), which the binary
run of the solver-checks harness used and the owner has not confirmed. `rubric_report_v3` carries
the same wording for both subsets; if the owner judges it beyond a harness-level hint, the bullet
is removed and both subsets are rerun.

## 9. Implementation and where it differs from this design (2026-11-20) ✅

`tools/dar_terminus2_srvc.py`, class `Terminus2SRVC`, on top of the solver-checks class
(`10`). Run with `AGENT=t2srvc` (`tools/run_dar_qwen35_35b.sh`); every number below is an
`--ak` argument. Skills: `tools/skills/solver_v5`, `reader_v2`, `compaction_v1`,
`rubric_section_table_v1`, `rubric_fact_table_v1`, `rubric_grounded_reasoning_v1`,
`rubric_report_v3`; solver template `tools/templates/terminus-json-multiagent-v5.txt`.

| Part | What the code does | Tunables (default) |
| --- | --- | --- |
| First message | Task and the solver skill. The rule document is replaced by a note that it comes through the reader, unless it is at most `solver_rule_inline_max_chars`. | `solver_rule_inline_max_chars` (0) |
| Survey | One reader call before the solver's first step, as a branch of the first message. Its outline and excerpts are appended to the first message. If it returns nothing, the first message says so and tells the solver to call the reader. | `reader_survey` (true), `reader_survey_max_chars` (20,000) |
| Reader call by the solver | A branch of the solver's messages. The source is printed with line numbers in the branch message up to `reader_inline_max_chars`; a larger one is read with commands. Ranges already in the solver's context are dropped. | `reader_inline_max_chars` (60,000), `reader_max_steps` |
| Direct range | A query that is only line ranges ("lines 40-80") is answered by the harness from the file, without a model call. | `reader_direct_ranges` (true) |
| File write | `"write_file": {"path", "content"}` in the solver's reply (a list for several files). The harness uploads the file and answers with one line: path, lines, characters, last line. | none |
| Verifier | One round, three rubrics in independent contexts, thinking kept on a cut reply. A second call returns the stored report and uses no model call. | `max_verify_rounds` (1), `rubric_max_output_tokens` (8,000) |
| Completion | After the round: accepted (`verified`, `revised_after_report` or `unrevised_not_passed`). Before a round: bounced up to `max_unverified_claims` times, then accepted as `unverified_accepted`. | `max_unverified_claims` |
| Compaction | Called by the solver, or by the harness when the next solver prompt is estimated above the window. A branch writes the state page without thinking; the harness rebuilds the context as first message, state page with pinned items, last turns. | `context_window` (32,000), `compaction_keep_turns` (2), `compaction_min_tokens` (window / 2), `compaction_max_output_tokens` (2,000), `compaction_min_saving` (0.2) |
| Kept thinking | A reply cut at its output limit is resent without thinking, with the cut reply's reasoning and partial content as "your notes so far". | `keep_thinking` (true), `keep_thinking_max_chars` (40,000) |
| Cost | Ledger per call and role; the budget counts the with-cache price. | `cost_budget_usd` (0.15), `cost_budget_basis` (`cache`) |

Logs per trial under `agent/`: `llm_calls.jsonl`, `reader/call-N.json`,
`compaction/call-N.json` (with the context before and after and the state page),
`write_file/NNN.txt`, `rubric_verifier/round-1.json`, `rubric_verifier/summary.json`
(`"harness": "srvc-v0.1"`). `tools/sc_run_report.py` prints the SRVC lines when it sees that
field; `tools/inference_cost.py` reports cost per role.

Differences from §3 and §4:

- **Compaction output limit** is 2,000 tokens, not 1,500, as a margin for the JSON wrapping of
  the state page. 🔶 Not tuned.
- **Direct line ranges** are new. The outline gives the solver line ranges; fetching one is a
  lookup, so it does not need a model.
- **Outline coverage** is checked by the harness: the outline rows must cover the document's
  non-blank lines (up to 2% or 5 lines), else the reader is asked once to repair it.
- **Every message of a branch ends with the role and its reply format.** In the first smoke
  (`srvc-smoke1`, 2 tasks) one survey reply was cut at the output limit inside its thinking;
  the resend without thinking then answered in the solver's format three times and the survey
  returned nothing. The branch message ended with "as described in the skill", and the solver's
  own instructions are also a skill. The reader and compaction closing lines now name the role
  and the fields that do not belong in the reply, and the notes block is followed by the same
  line.
- **Reader skill, two additions after the first smoke** (generic, no task content): the reader
  does not work the case out in its reasoning; and when a unit sets out parallel alternatives
  and the choice depends on a determination about the case, it returns all of them. In the
  smoke the reader had returned one rate table of four and the solver used it without question;
  the rubrics caught it.
- **JSON decoder hint.** When a solver reply is not valid JSON, the stock message says only "No
  valid JSON found". The harness adds the decoder's position and the text around it. Three of
  the 11 solver replies of one smoke trial had a mismatched bracket in a long command.
- **Later reader calls are not cheap.** A reader branch after the survey carries the numbered
  source again (about 8,000 tokens not in the cache, about 0.11 cents), because the solver's
  context holds excerpts and not the document. 🔶 Putting the numbered source at a fixed place
  of the branch prefix would not help either: the prefix is the solver's context, which grows.
- The parent's messages for calls that use no round still say "check" in two places.

## 10. Smoke runs and the first full runs (2026-11-20)

All runs: Qwen3.5-35B-A3B on the local vLLM server, T 0.7, thinking on, no time-outs, cost
budget 0.15 USD per task at the with-cache price. Cost is in US cents per task on the price
card of `09` §10.12, written *no cache / with cache*; the with-cache number is the headline
(§5). Reports: `tools/sc_run_report.py` and `tools/inference_cost.py`.

### 10.1 Smoke runs

| Job | Tasks | Settings | Correct | Cost | What it showed |
| --- | --- | --- | --- | --- | --- |
| `srvc-smoke1` | 2 | defaults | 2/2 | 7.19 / 5.84 | ✅ Survey, `write_file`, one verifier round with three rubrics, completion gate. |
| `srvc-smoke2` | 4 | `context_window=12000` | 2/4 | 5.51 / 4.51 | ✅ 13.2 model calls per task. Five compaction calls, all declined (2 `nothing_to_remove`, 3 `not_smaller`): the decline paths work, the applied path was not reached. |
| `srvc-smoke3` | 2 | `context_window=12000 compaction_min_saving=0.03`, turn-based compaction | 1/2 | 4.79 / 4.15 | Two compaction calls, both `nothing_to_remove` (three turns). The applied path was first reached in `kirasrvc-smoke2` (§11) and once in the full numeric run (§10.4). |

### 10.2 Full runs: accuracy and cost ✅

`srvc-run1-numeric` (35 tasks) and `srvc-run1-binary` (30 tasks), started 05:09 UTC from commit
`b158eddd`, 8 trials at a time each on one server, default 32K window. No trial ended with an
exception, none was stopped by the cost budget. Binary ended 10:12 UTC, numeric 11:22 UTC.
Cost detail per trial and role: `outputs/srvc_run1_{numeric,binary}_inference_cost.json`.

| Run | Correct | Wilson 95% | Calls per task | Cost no cache | Cost with cache |
| --- | --- | --- | --- | --- | --- |
| `srvc-run1-numeric` | 17/35 (48.6%) | 33.0-64.4% | 14.7 | 6.01 | **4.74** |
| `sc-run1-numeric` (solver-checks, `10` §7) | 19/35 (54.3%) | | | 9.40 | 7.20 |
| reader-verifier-compactor (`09` §10.12) | 17/35 | | | 6.51 | |
| solver-verifier (`08` §10) | 15/35 | | | 7.07 | |
| terminus-2, our rerun under the cost protocol | 2/35 | | | 1.75 | |
| `srvc-run1-binary` | 24/30 (80.0%) | 62.7-90.5% | 11.9 | 3.68 | **3.04** |
| `sc-run1-binary` | 23/30 (76.7%) | | | 5.59 | 4.17 |

- ✅ **Cost fell by about a third against the solver-checks harness**: numeric 7.20 to 4.74
  cents with cache (-34%; -36% without), binary 4.17 to 3.04 (-27%; -34% without).
- **Accuracy is not distinguishable from the solver-checks harness.** Task by task on numeric:
  15 correct in both runs, 4 only in `sc-run1`, 2 only in `srvc-run1`; on binary 21 / 2 / 3.
  With 35 and 30 tasks, one run each at T 0.7, a difference of two tasks is noise. 🔶 The claim
  the runs support is "same accuracy range at two thirds of the cost", not an accuracy gain or
  loss.

- ✅ **SARA binary macro-F1** (the DAR paper's metric for this subset; rule of
  `harbor-deonticbench/evaluate_deonticbench.py`): `srvc-run1-binary` **0.799** (contradiction
  P 0.812 / R 0.812, entailment P 0.786 / R 0.786; gold 16 contradiction, 14 entailment; every
  trial gave one of the two labels, so there is no abstention to map). `sc-run1-binary` by the
  same rule: 0.777.

Per role, mean per task (share of the no-cache cost):

| Role | Numeric: calls | no cache | with cache | Binary: calls | no cache | with cache |
| --- | --- | --- | --- | --- | --- | --- |
| solver | 7.7 | 2.65 (44%) | 1.87 | 6.0 | 1.11 (30%) | 0.79 |
| reader | 2.1 | 1.02 (17%) | 0.85 | 1.9 | 0.73 (20%) | 0.64 |
| rubric: section table | 1.7 | 0.86 (14%) | 0.80 | 1.3 | 0.66 (18%) | 0.64 |
| rubric: grounded reasoning | 1.8 | 0.78 (13%) | 0.65 | 1.5 | 0.57 (15%) | 0.46 |
| rubric: fact table | 1.4 | 0.67 (11%) | 0.56 | 1.3 | 0.61 (17%) | 0.51 |
| compaction | 0.03 | 0.02 (0%) | 0.01 | 0 | 0 | 0 |

### 10.3 What the verifier round did ✅

The answer in the file when the verifier was called, against the final answer:

| | Numeric | Binary |
| --- | --- | --- |
| Correct before the verifier | 13/35 | 26/30 |
| Correct at the end | 17/35 | 24/30 |
| Wrong before, right after | 7 | 1 |
| Right before, wrong after | 3 | 3 |
| First report passed: trials (correct before) | 9 (6) | 17 (15) |
| First report did not pass: trials (correct before) | 26 (7) | 13 (11) |

- ✅ **Same pattern as the solver-checks harness, smaller on numeric.** On numeric the one
  round is worth four tasks net (7 gained, 3 lost; 13 to 17). `sc-run1-numeric`, with up to
  three rounds and rebuttals, gained 8 and lost 1 (12 to 19, `10` §7.1). The solver before the
  verifier is at the same level in both (13 and 12 of 35): what differs is the round, at a
  third less cost.
- ✅ On binary the round costs two tasks net (1 gained, 3 lost; 26 to 24), as it did in
  `sc-run1-binary` (0 gained, 2 lost; 25 to 23, `10` §7.2). The solver is right before the verifier in
  26 of 30 cases, and the rubrics still report findings in 11 of those 26. A report that does
  not pass says little there: 11 of the 13 answers it was raised against were correct.
- 🔶 One round with no second look means a wrong revision is final. Whether the solver should
  be told how often findings are wrong, or the report should carry a severity, is a skill
  question and is open.

Finish reasons (trials, correct): numeric `revised_after_report` 25 (11), `verified` 9 (6),
`unrevised_not_passed` 1 (0); binary `verified` 17 (15), `revised_after_report` 10 (6),
`unrevised_not_passed` 3 (3).

### 10.4 How the parts were used ✅

- **Reader.** One survey per trial. The solver asked for more text in 11 of 35 numeric trials
  (18 calls, 2 served as direct ranges) and 9 of 30 binary trials (12 calls).
- **Compaction.** One call in 65 trials, applied (numeric): 7 steps removed, 102,596 to 55,730
  characters, solver prompt 31,541 to 19,200 tokens. The solver's prompt at the last step has
  a median of 15,925 tokens on numeric (max 27,980) and 8,534 on binary (max 14,207): with a
  32K window SARA does not need compaction. No solver called it by itself.
- **Kept thinking.** 40 cut replies on numeric and 11 on binary had their thinking handed to
  the next call (numeric: section table 16, grounded reasoning 10, fact table 8, reader 6).
  Under the solver-checks harness that output was paid for and thrown away (§2).
- **`write_file` is used by fewer than half of the solvers.** Numeric: 10 trials wrote the
  answer file only through the field, 21 only from the terminal (heredoc, then `cat`), 4 both
  ways; binary 11 / 16 / 3. Template and skill ask for the field in both runs, and all six
  smoke trials used it. 🔶 On numeric the terminal-only trials cost more (6.49 / 5.02 cents
  and 8.6 solver calls against 4.77 / 3.91 and 5.9) and were right as often or more (12 of 21
  against 4 of 10); on binary there is no cost difference (3.47 / 2.89 against 3.52 / 2.93).
  This is a comparison of self-selected groups, not an effect of the field. A generic note
  ("the answer file changed without `write_file`") was considered and not added.

### 10.5 A harness bug in these runs: a `write_file` lost without a note ✅

Found while the runs were going, fixed in commit `1eafe0a0`; **the runs above are on the code
before the fix.**

- **What happened.** A solver reply that never closes its `"write_file"` object
  (`"write_file": {"path": ..., "content": "...", "task_complete": false }`) is repaired by
  the stock terminus-2 parser, which appends the missing brace and runs the commands. Our
  reading of the same reply (`_extract_json_object`) returned the first balanced object, which
  is the inner one, so the harness saw no `write_file` field: the file was not written and the
  solver was not told. In one trial the inner object also held the `subagent` field, so the
  verifier was called while the write was dropped.
- **Fix.** `reply_object()` in `dar_terminus2_multiagent.py`: when objects are left open at
  the end of the reply, close them the way the stock parser does and read the top-level
  object. Checked against all 841 solver replies of the earlier jobs: 37 are read differently,
  each time the top-level object instead of an inner one.
- **Extent in the full runs.** 7 of 65 trials have at least one reply that the two readings
  disagree on: numeric 2 (1 correct, 1 wrong), binary 5 (all correct). Six of the seven wrote
  the file from the terminal afterwards, one with a later `write_file`. 🔶 The effect on the
  outcome is not established; the affected numeric trials cost 9.46 cents against a mean of
  6.01, which is two trials. The runs were not restarted: the fix landed 85 minutes into a
  six-hour run, and a rerun on the fixed code is the clean number (§10.6).

### 10.6 Not done, and run notes

- 🔶 **No rerun on the fixed code.** `srvc-run1` is one run per subset on the code before
  `1eafe0a0`. The table for the paper should come from a rerun (`srvc-run2-*`).
- 🔶 **Only SARA.** Airline and USCIS have not been run with this harness.
- **Server.** vLLM was started for these runs and stopped at 16:26 UTC, five hours after the
  last run ended at 11:22 UTC. The waiter that was to announce the end used
  `pgrep -f "job-name srvc-run1"`, which matched its own command line and never returned. The
  server held GPU memory idle for that time, against the rule for this machine.

## 11. The harness on KIRA's loop (2026-11-20)

`tools/dar_kira_srvc.py`, class `KiraSRVC`, run with `AGENT=kirasrvc`. KIRA
(`agents.baseline_kira`, DAR's second harness) is a terminus-2 subclass with native tool
calls and its own agent loop. The adapter does not copy the harness: it puts KIRA *under* the
SRVC classes in the class order,

    KiraSRVC -> Terminus2SRVC -> ... -> CostLedgerMixin -> _KiraUnderSRVC
             -> dar_kira_local.AgentHarness -> baseline_kira.AgentHarness -> Terminus2

so the two hooks in which the harness lives (`_handle_llm_interaction`: what did the solver
ask for; `_execute_commands`: do it and build the next message) end in KIRA's versions, and
KIRA's loop, marker polling and tool-call history stay as they are. ✅ This is the evidence
for the claim of §6 that the harness is separate from the base agent: the adapter is about
300 lines, and the shared code needed two generic changes (below), none specific to KIRA.

| Part | What the adapter does |
| --- | --- |
| Tools | `write_file`, `reader`, `verifier`, `compaction` are added to `execute_commands` and `task_complete`. `image_read` is not offered: its path through KIRA's loop does not pass `_execute_commands`, where the completion gate is. |
| Reply | `_KiraUnderSRVC` takes the four tools out of the reply's tool calls and hands the classes above a JSON object of the shape they read from a terminus-2 reply (`commands`, `subagent`, `write_file`, `task_complete`). One sub-agent per response; a second one is not run and the solver is told. |
| Model calls | Through harbor's LLM object instead of KIRA's direct `litellm` call, so `max_tokens`, the request time-out and the cost ledger are those of the terminus-2 version. Tool calls are read from the raw reply. |
| Branches | The reader and compaction requests carry the same tool definitions with `tool_choice="none"`. ✅ vLLM keeps the definitions in the prompt in that case (26 prompt tokens without tools, 281 with tools, 281 with tools and `tool_choice="none"`), so the branch shares the solver's prefix. The rubrics have their own context and no tools. |
| Wording | The harness's messages name JSON fields (`"subagent": {"name": "verifier"}`). `tool_wording` and `branch_wording` rewrite those phrases for a solver that calls tools. 🔶 A phrase table, not parameterised messages: a message added to the harness later has to be checked against it. |
| Skill and template | `tools/skills/solver_v5_tools` (solver v5 with the calls as tools) and `tools/templates/terminus-kira-srvc.txt` (KIRA's own text plus the rule and skill slots). Reader, compaction and rubric skills are the same files. |
| Time limits | KIRA's 600 s limit per step is switched off: a verifier round takes longer, and the protocol has no wall-clock limits. |
| Cut solver reply | KIRA asks once for a shorter reply. A second cut reply in the same step would end the trial with an exception in KIRA; the adapter passes it on as a reply without tool calls. |

Generic changes to shared code that the adapter needed:

- **Compaction counts turns, not message pairs** (`split_turns` in `dar_terminus2_srvc.py`).
  A turn is a user message, the assistant's reply and the tool messages that close its calls.
  For a terminus-2 context (strict user/assistant alternation) the result is the same.
- **The ledger's prompt text includes tool definitions and tool calls** (`prompt_text` in
  `dar_cost_ledger.py`), so that the simulated cache does not count a request with tools and
  one without as sharing a prefix. Unchanged for requests without tools.
- **Misplaced calls**: the patterns for a sub-agent call typed as a keystroke are a class
  attribute; the adapter adds "a keystroke that starts with a tool name".

Smoke runs (Qwen3.5-35B-A3B, SARA numeric; cost in US cents per task, no cache / with cache):

| Job | Tasks | Settings | Correct | Cost | What it showed |
| --- | --- | --- | --- | --- | --- |
| `kirasrvc-smoke1` | 1 | defaults | 0/1 | 12.27 / 8.82 | ✅ The loop runs end to end: survey, tool calls, reader branches that share the solver's prefix (9,137 of 20,452 prompt tokens read from the cache), verifier round, completion. The reader took 54% of the cost: five queries, 13 model calls, no line inserted, because the solver asked for units the survey had already put in its context ("lines 95-179 of /app/statute.txt · §152. Dependent defined"). With nothing to return, the branch went on as the solver in two of the calls (worked the case out, wrote tool calls as text). The calls reached nothing, but they cost. |
| `kirasrvc-smoke2` | 2 | `context_window=12000 compaction_min_tokens=4000 compaction_min_saving=-1` (compaction forced) | 2/2 | 6.30 / 4.82 | ✅ After the two reader fixes below: two queries that began with a line range were answered by the harness without a model call, one query went to a branch. ✅ Compaction applied three times on a tool-call conversation (15 messages to 9; the rebuilt context keeps each assistant message with its tool message) and the solver went on after each one. |

Changes after `kirasrvc-smoke1`, all in the shared reader code and none specific to KIRA:

- **A query that begins with line ranges is a direct range** (`direct_range_text`): the
  outline prints "lines a-b" next to each unit and the solver copies the row. Text after the
  ranges is ignored unless it names another file.
- **A reader result that inserts nothing says why**: "The reader leaves out lines that are
  already in your context: ...", with the ranges. Before, the result said only "No rule text
  was inserted" and the solver asked again in other words.

One of the three applied compactions in `kirasrvc-smoke2` made the solver's prompt longer
(14,748 to 14,848 tokens): the run switched the saving guard off (`compaction_min_saving=-1`)
to force the applied path. With the default guard (0.2) that call is declined.

### 11.1 Full runs on SARA (2026-11-20) ✅

`kirasrvc-run1-numeric` (35 tasks) and `kirasrvc-run1-binary` (30 tasks), started 16:35 UTC
from commit `46499edd` (with the `reply_object` fix of §10.5 and the two reader fixes above),
8 trials at a time each on one server, default settings, same protocol as §10. No trial ended
with an exception, none was stopped by the cost budget. Binary ended 20:53 UTC, numeric 22:13
UTC; the waiter stopped the server in the same command (22:14 UTC, no vLLM process, no GPU
process). No KIRA baseline was run (owner, 2026-11-20: the harness only). Cost detail:
`outputs/kirasrvc_run1_{numeric,binary}_inference_cost.json`.

| Run | Correct | Wilson 95% | Calls per task | Cost no cache | Cost with cache |
| --- | --- | --- | --- | --- | --- |
| `kirasrvc-run1-numeric` | 18/35 (51.4%) | 35.6-67.0% | 16.0 | 5.78 | **4.40** |
| `srvc-run1-numeric` (terminus-2 loop, §10) | 17/35 (48.6%) | 33.0-64.4% | 14.7 | 6.01 | 4.74 |
| `kirasrvc-run1-binary` | 26/30 (86.7%) | 70.3-94.7% | 11.4 | 3.37 | **2.77** |
| `srvc-run1-binary` (terminus-2 loop, §10) | 24/30 (80.0%) | 62.7-90.5% | 11.9 | 3.68 | 3.04 |

- ✅ **The harness gives the same result on the second base agent**: accuracy within one or
  two tasks of the terminus-2 version on both subsets, cost 7% and 9% lower with cache. Task
  by task against `srvc-run1`: numeric 14 correct in both, 3 only on terminus-2, 4 only on
  KIRA; binary 22 / 2 / 4. 🔶 One run each; the differences between the two base agents are
  not significant, and `srvc-run1` carries the bug of §10.5 while this run does not.
- ✅ **SARA binary macro-F1**, same rule as in §10.2: `kirasrvc-run1-binary` **0.866**
  (contradiction P 0.875 / R 0.875, entailment P 0.857 / R 0.857; no abstention), against
  0.799 on the terminus-2 loop.
- **The hard tasks are the same ones.** On numeric, 12 tasks are right in all three runs
  (`sc-run1`, `srvc-run1`, `kirasrvc-run1`) and 12 in none; on binary 20 and 2.

What the verifier round did (the answer in the file when the verifier was called, against the
final answer):

| | Numeric | Binary |
| --- | --- | --- |
| Correct before the verifier | 13/35 | 24/30 |
| Correct at the end | 18/35 | 26/30 |
| Wrong before, right after | 9 | 3 |
| Right before, wrong after | 4 | 1 |
| First report passed: trials (correct before) | 8 (4) | 19 (16) |
| First report did not pass: trials (correct before) | 27 (9) | 11 (8) |

- ✅ Numeric: five tasks net (13 to 18), the same direction and size as on terminus-2 (13 to
  17). The solver before the verifier is at 13 of 35 on both base agents.
- 🔶 Binary: two tasks net *gained* (24 to 26), where both terminus-2 runs lost two (§10.3).
  With three or four changed answers per run this is not a difference between base agents;
  taken together the three binary runs (`sc-run1` -2, `srvc-run1` -2, this run +2) say that
  the verifier changes the outcome of two to four answers and that no gain on binary is shown.

How the parts were used:

- ✅ **`write_file` as a tool is used by almost every solver**: 35 of 35 numeric trials and 29
  of 30 binary trials wrote the answer file through it (66 and 42 files); 4 and 5 trials also
  wrote it from the terminal at some point. On the terminus-2 loop, where it is a field of the
  JSON reply, fewer than half did (§10.4). A native tool is a stronger signal to this model
  than a field described in the prompt.
- **Reader.** One survey per trial. The solver called the reader in 19 of 35 numeric trials
  (43 calls: 35 direct ranges, 8 model branches) and 16 of 30 binary trials (27 calls: 24
  direct, 3 branches). 22 and 16 of those calls inserted nothing because the lines were
  already in the solver's context: the solver still asks for what it has, but since the fix
  after `kirasrvc-smoke1` such a call costs a solver step and no reader branch. The reader's
  share of the cost is 14% and 16% (17% and 20% on terminus-2).
- **Compaction** was not called in either run (solver prompt at the last step: median 14,753
  tokens on numeric, max 25,550; median 7,843 on binary). The applied path on a tool-call
  conversation is covered by `kirasrvc-smoke2` only.
- **Kept thinking.** 33 cut replies on numeric and 15 on binary were handed to the next call.

### 11.2 Cost of the KIRA baseline from the runs of 2026-10-16 (estimate, 2026-11-22) 🔶

No KIRA baseline was run under the cost protocol. The only baseline KIRA trials with this model
are `sara-kira-run1` plus `sara-kira-rerun9` (`06` §1, wrapper-repaired set: 56 finished trials
and the 9 that were rerun after the crash fix). Priced on the same price card:

| Run | Correct | Calls per task | Input / output tokens | Cost no cache | Cost with cache |
| --- | --- | --- | --- | --- | --- |
| KIRA baseline, numeric | 11/35 | 8.9 | 83,052 / 8,556 | 2.02 | 1.37 |
| terminus-kira-harness v0.1, numeric (§11.1) | 18/35 | 16.0 | 201,184 / 29,618 | 5.78 | 4.40 |
| KIRA baseline, binary | 26/30 | 7.3 | 63,172 / 3,646 | 1.25 | 0.78 |
| terminus-kira-harness v0.1, binary (§11.1) | 26/30 | 11.4 | 101,699 / 19,449 | 3.37 | 2.77 |

- **How it was computed.** `tools/inference_cost.py` does not read these trials (KIRA calls the
  model outside harbor's LLM object, so there is no ledger and no `debug.json`). The token
  counts are the per-step `metrics` of `agent/trajectory.json`, whose sums equal the totals in
  `result.json` in all 65 trials ✅. The no-cache cost is exact. The with-cache cost assumes
  that each request reads the previous request's prompt from the cache; the prompts grow
  strictly in all 65 trials, so this is the same rule the ledger applies to an append-only
  conversation, but it was not computed by the ledger.
- 🔶 **Not the same protocol.** These trials ran with the 1,800 s limit (none timed out) and
  before the cost budget existed; nine tasks are a second draw. For terminus-2 the rerun under
  the cost protocol moved the baseline from 7/35 to 2/35 (`09` §10.12), so a number from an
  older run is an estimate, not the baseline row of a table.
- **What it says if it holds.** On numeric the harness is worth seven tasks (11 to 18) at 3.2
  times the with-cache cost. On binary the baseline is already at 26/30 and the harness costs
  3.6 times as much for the same count.
