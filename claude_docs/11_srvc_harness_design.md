# 11 · Solver-reader-verifier-compaction harness: design of v0.1

**Status (2026-11-20): design, not implemented.** §1 records what the owner has decided
(2026-11-19 the frame, 2026-11-20 the verifier, the window, the cost column). §8 lists what the
owner still has to confirm before the implementation starts. The numbers in §2 are measured on
finished runs with `tools/sc_check_stats.py`.

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

## 8. To confirm before the implementation starts

- **The rule document leaves the solver's first message on SARA** and comes through the reader
  (outline plus excerpts). This is what "with a reader" means on a 6,097-token document, and it
  is the main accuracy risk of v0.1 (§3.2).
- **Naming**: check becomes rubric, its table a rubric table (as read from the owner's message).
- **The file-write channel** of §3.4 as the answer to the repeated answer file.
- **Rubric output limit** stays 8,000 (§4.1).
- **Budget** at the price with cache (§5).
- **Thinking in the branches.** Proposed: the reader thinks, with the same keep-the-thinking
  rule as the rubrics; compaction writes the state page without thinking.
- Still open from `10` §8: report format v2 (the case text read as complete), which the binary
  run used and the owner has not confirmed.
