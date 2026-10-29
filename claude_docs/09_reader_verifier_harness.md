# 09 · Reader-Verifier Harness (solver calls reader and verifier sub-agents as tools)

- **Name**: the **reader-verifier harness** (owner, 2026-10-28), version 1 of the planned
  reader-verifier-compactor harness. A terminus-2 solver that obtains rule text through a
  **reader** sub-agent and must have its answer file checked by the **verifier** sub-agent
  before `task_complete` is accepted. Both sub-agents are called by the solver through a field
  in its JSON response, run in their own `Chat`, and return a text result into the solver's
  conversation. Version 2 (a **compactor** that rebuilds the solver's context before a repair)
  is deferred and not designed here.
- **Date opened**: 2026-10-28
- **Predecessor**: the solver-verifier harness ([08](08_solver_verifier_harness.md)), run-3
  configuration. Its verifier (skill v3, four-step plan, index packet) is reused with a skill
  v4 (four rubrics, §5); its completion protocol is replaced (§3).
- **Code**: `tools/dar_terminus2_multiagent.py` (`Terminus2ReaderVerifier`, a subclass of the
  run-3 verifier agent); one skill per role, `tools/skills/solver_v1/SKILL.md`,
  `tools/skills/reader_v1/SKILL.md`, `tools/skills/rubric_verifier_v4/SKILL.md`;
  `tools/templates/terminus-json-multiagent-v1.txt` (the stock terminus-2 template plus the
  `subagent` field and a `{skill}` slot); `tools/run_dar_qwen35_35b.sh` with `AGENT=t2rv`.
- **Confidence convention**: ✅ checked on the machine / in run output on the date given,
  🔶 expectation or inference.

## 1. Why: the solver's context is the problem now

Run-3 of the solver-verifier harness ([08](08_solver_verifier_harness.md) §9.2) is the first
terminus configuration that beats the no-verifier baseline on SARA numeric (0.400 vs 0.200,
protocol score), but it is still only at the low edge of the CI of prompt-based direct reasoning
(0.435), which sees the whole 6.1k-token statute in one prompt and nothing else. Three things in
the solver's trajectory account for most of the gap and for the time it costs:

1. **The solver's own search.** A numeric trial spends 6 steps (median) before its first claim
   `grep`-ing and `sed`-ing the statute, each step a thinking-bearing generation at ≈59 ms/token.
   The terminal output of those steps (10,000-byte observations, truncation markers) stays in the
   conversation for the rest of the trial.
2. **Repair turns compound it.** After a verifier report the solver reads more sections and the
   conversation grows again; the three binary damages of run-3 and the right→wrong numeric cases
   all happened in repair turns.
3. **Statute size.** SARA's statute is only 6.1k tokens, so a direct prompt loses nothing by
   including all of it. Any retrieval step can only *remove* information relative to direct; the
   harness has to win on cleaner context, tool-executed arithmetic and verification, not on
   retrieval.

The reader-verifier harness moves the search into a sub-agent whose internal commands and
outputs never enter the solver's conversation. What the solver receives is the **verbatim rule
text** of the ranges the reader selected, extracted from the file by the harness, plus the
reader's one-line relevance notes and a list of what it did not resolve. The verifier is then
called explicitly by the solver instead of being triggered by `task_complete`, and the harness
only checks that the answer file the solver finishes with is the one the verifier last saw.

Design decisions in this document were taken with the owner on 2026-10-28 (decision log in
[04](04_initial_proposal.md) §8). GPT's plan of the same date (two-stage, sub-agents as tools,
no Compactor in version 1, no `state.md`) is the starting point; deviations are marked.

## 2. Components and the call mechanism

| Role | Context | Tools | Called by | Returns |
|---|---|---|---|---|
| **Solver** | terminus-2 conversation (stock loop, JSON responses) | tmux shell (unchanged), two sub-agents | Harbor | the answer file |
| **Reader** | fresh `Chat` per call | `environment.exec` in the task container, ≤ `reader_max_steps` command steps (default 3), 6,000 bytes of output per command | solver, any number of times | line ranges + labels + unresolved items; the harness turns the ranges into verbatim text |
| **Verifier** | fresh `Chat` per round | `environment.exec`, ≤ `verifier_max_steps` steps (default 4, as in run-3) | solver, ≤ `max_verify_rounds` times (default 3) | the rubric report, rendered as in run-3 |

**Call mechanism (owner's decision 2026-10-28)**: a top-level field in the solver's JSON
response, at the same level as `task_complete`:

```json
{"analysis": "...", "plan": "...", "commands": [...],
 "subagent": {"name": "reader", "query": "which provisions govern ..."}}

{"analysis": "...", "plan": "...", "commands": [],
 "subagent": {"name": "verifier"}}
```

Harbor's JSON parser ignores unknown top-level fields, so the stock parser, its auto-fixes and
its warnings are untouched; the subclass reads the field from the raw response. In one episode
the commands run first (through tmux, as always), then the sub-agent; the sub-agent's result is
appended to that episode's terminal output as the next message to the solver. A response that
both calls a sub-agent and sets `task_complete` is treated as a call, not as a finish: the
completion flag is ignored and the result says so. Native function calling was not used because
the terminus-2 loop is text-based end to end and the DAR baseline is terminus-2 (plan §2.1 left
this open; decided for the JSON field).

Nothing is intercepted at the shell level. The solver may still read the rule file itself; the
prompt tells it to use the reader, and the harness counts the solver's own reads of the rule file
in `summary.json` (owner's decision: no hard-coded interception).

## 3. Completion protocol

Stock terminus-2 ends a trial on the second consecutive `task_complete` ("Are you sure?" in
between). The solver-verifier harness kept that and put the verifier in the confirmation
message. Here the verifier is called by the solver, so the protocol changes (owner's decisions
2026-10-28):

| Event | What the harness does |
|---|---|
| Solver calls `verifier` | commands of that episode run; the answer file is read and hashed; verifier round r runs (r ≤ `max_verify_rounds`); the report plus a decision instruction is returned; the hash is recorded as **verified** |
| Solver calls `verifier` with no rounds left | no verifier; the message says so and that `task_complete` will now be accepted as the file stands |
| `task_complete`, hash equals the last verified hash | commands of that episode run first and the file is re-hashed afterwards; if still equal, the **trial ends at once** (no "Are you sure?" step; owner's decision) |
| `task_complete`, hash not verified, rounds remain | not accepted; the message says the current file has not been verified and asks for a verifier call. Counted as an *unverified claim*. After `max_unverified_claims` (default 2) such claims the next one is accepted and the finish is recorded as `unverified_accepted` so the trial cannot loop until the wall clock |
| `task_complete`, hash not verified, no rounds left | accepted; finish recorded as `budget_exhausted` |
| Sub-agent call and `task_complete` in one response | the call runs, `task_complete` is ignored, the result says so |

The instruction to the solver says the rule in words: "call the verifier before finishing; if
you change the answer file after a report, call it again". The harness gate is the mechanical
backstop for a solver that does not follow it (run-3 showed the Qwen verifier ignoring skill-level
budget rules in most later rounds; the solver is not assumed to be more obedient). The gate holds
no checking logic: it compares two hashes and counts rounds. Whether the report was all-`YES` is
irrelevant to it (plan §2.6: verified ≠ correct).

Hashing after the episode's commands closes the "write a new answer in the confirming response"
path that run-1..3 only logged as `unverified_finish`.

## 4. The reader

### 4.1 Inputs (one user message = `SKILL.md` + packet)

```
=== TASK ===
<instruction.md verbatim: facts, question, rule-file path, answer-file path>

=== QUERY (from the solver, call 2 of this task) ===
<the solver's query text>

=== RETURNED EARLIER IN THIS TASK ===
call 1: lines 87-131 (§63(c)(2) standard deduction); lines 412-440 (§3301 rate of tax)

=== BUDGET ===
3 command steps, 6,000 bytes of output per command, 16,000 characters of excerpts in total.
```

The reader sees the task text so that it can locate provisions the facts engage even when the
query does not name them (plan §2.2; owner 2026-10-28: "does not cost many tokens"). The skill
tells it that the **query decides what to return** and the facts serve to resolve the query's
terms and to pick the exceptions that apply; it does not decide how a provision applies. It does
**not** see the answer file or the solver's conversation. The "returned earlier" index is built
by the harness from the reader's own previous results (ranges and labels only); it is a
de-duplication hint, not shared state.

### 4.2 Output and extraction

Every reader reply is one JSON object: `analysis`, `commands`, `result`. `result` is `null`
while it works and, when done,

```json
{"excerpts": [{"lines": "87-131", "label": "§63(c)(2) standard deduction amounts",
               "why": "the taxpayer takes the standard deduction"}],
 "unresolved": ["whether the father is a dependent: §152(d) read, support test not in the facts"]}
```

An excerpt may name a `file`; by default the ranges refer to the rule file (`rule_path`,
default `/app/statute.txt`, DAR's convention in all four subdomains). The harness checks each
range mechanically (two integers, `a ≤ b`, within the file's line count), runs `sed -n 'a,bp'`
on the file in the container and inserts the lines verbatim. Invalid ranges are reported as such
in the result, never guessed or repaired. The total is capped at `reader_max_chars` (default
16,000; the whole SARA statute is ≈24,000 characters): the excerpt that crosses the cap is cut
with a marker giving the lines shown and omitted, and later excerpts are listed by range and
label as not inserted. The model never copies statute text itself; that was ruled a hard
requirement because a 6k-token copy would cost about six minutes of generation.

What the solver receives:

```
=== READER RESULT (call 1 of this task) ===
Query: ...

[1] lines 87-131 of /app/statute.txt · §63(c)(2) standard deduction amounts
    why: the taxpayer takes the standard deduction
<lines 87-131, verbatim>

[2] lines 412-440 of /app/statute.txt · §3301 rate of tax
    why: ...
<verbatim>

Unresolved:
- ...

(2 excerpts, 2,310 characters, nothing truncated; 2 command steps used)
```

A reader call that returns no `result` within its budget (after one closing prompt and one
retry, as for the verifier) produces a result block that says so and lists nothing; the solver
decides what to do next. A reader call with a missing or empty query is answered by the harness
with a one-line error and no sub-agent run.

### 4.3 Skill v1 (`tools/skills/reader_v1/SKILL.md`, 66 lines)

The reader's stated goal is **coverage**, defined in the same words as the verifier's rubric 2: a
provision is covered when its text, with the definitions, conditions, exceptions and
cross-references it depends on, has reached the solver. Then eight one-line ground rules (the
query decides what to return; whole provisions; only lines displayed in this call; say what is
unresolved; never answer; never paraphrase; the file is the only authority; read-only), the
response format and 3-step budget, the inputs, the three steps (map and locate, read, decide
and return, with the `§`-nesting note) and the result schema. Written for rule-grounded tasks in
general; SARA's `§` headings appear only as examples. The reader cannot *check* coverage: it has
neither the answer file nor the trajectory, and it is stateless across calls; that check is the
verifier's (owner, 2026-10-28).

## 5. The verifier (skill v4, `tools/skills/rubric_verifier_v4/SKILL.md`, 101 lines)

Mechanics are run-3's: index packet, full trajectory uploaded to the container and read by the
verifier with `grep`/`sed` (owner, 2026-10-28: the trajectory stays available), four-step plan
(locate, read, check, report), `verifier_max_steps=4`, one command step on later rounds, same
renderer. The trajectory index marks each step that called a sub-agent (`reader call N: lines …`,
`verifier round N`).

**Rubrics reorganised from five to four** (owner's decision 2026-10-28, after the question of
which checks belong to which agent). Mechanical checks were dropped rather than moved into the
harness: the answer-file format (three sections, `Answer:` last) is no longer checked anywhere
but stated once in the solver's skill (owner: no hard-coded gate; the solver can do this). The
order follows the proposal's logic ([04](04_initial_proposal.md) §2.1: tool use → coverage →
reasoning), selection before grounding:

| # | Rubric (`name` in the report) | Question | Evidence | `NO` when |
|---|---|---|---|---|
| 1 | Tool use (`tool_use`) | Did the tools deliver what the solver treated them as delivering, and was arithmetic done with a tool? | index; reader-result closing lines and "Not inserted" / "Invalid" lists in the trajectory | a reader call with no result, an invalid range or a truncated excerpt treated as delivered; a failed or truncated command treated as complete; arithmetic in prose |
| 2 | Provision coverage (`provision_coverage`) | Did the text the solver relies on, and the text the facts engage, reach the solver? | section map; `grep` of the trajectory for reader ranges and own reads; `sed` of the file | a cited section that does not exist in the file or never reached the solver (cited from memory); a section engaged by an `UNUSED` fact that never reached it |
| 3 | Provision selection and application (`provision_selection`) | Was the governing provision selected and were its conditions and exceptions checked against the facts? | reading the cited and the engaged sections against `## Reasoning` | an engaged provision never considered; a condition treated as met without a named fact; a triggered exception or limitation not applied. `UNUSED`-fact and entailment rules of v3 kept, shortened |
| 4 | Grounded reasoning (`grounded_reasoning`) | Does every number and section reference come from the file or the facts, and do the computations reproduce? | `sed` of the cited sections; `python3` re-run of the solver's stated steps | a section number that does not say what Reasoning uses it for; a number with no source; a computation that does not reproduce |

Relative to v3: rubric 1's format check is gone, its reader part is the new tool-use rubric;
v3's rubric 5 is split, "done with a tool" into rubric 1 and "reproduces" into rubric 4; v3's
rubrics 3 and 4 swap places. A provision counts as having **reached the solver** when its lines
are inside a `READER RESULT` block (the lines actually inserted, not a truncated or "not
inserted" part, nor what the reader read internally) or in the solver's own terminal output;
the solver not having run `grep`/`sed` itself is never a violation. NO vs UNKNOWN, point-do-not-
solve, actionable-only, all-pass ⇒ empty lists are unchanged from v3. The report schema keeps
`unused_facts`, `quoted_lines`, `unresolved` and `suggested_actions`; `RUBRIC_LABELS_V4` in the
agent renders the four names.

## 6. The solver's prompt: stock template + skill

Owner's decision 2026-10-28: the solver's prompt stays as close to stock terminus-2 as possible
and everything specific to this harness goes into a skill, so that all three roles have one.

- `tools/templates/terminus-json-multiagent-v1.txt` is the stock `terminus-json-plain.txt` with
  three additions: the `subagent` field in the JSON example, one line under "Optional fields"
  pointing at the skill, and a `Skill:\n{skill}` slot before `Task Description:`. The agent
  fills the slot from the skill file at start-up (braces doubled, since the template is
  `str.format`-ed with the instruction and terminal state later).
- `tools/skills/solver_v1/SKILL.md` (38 lines): how to call a sub-agent (one per response,
  commands first); the reader (verbatim text; query as the rule or question, not a guessed
  section number; first call broad, later calls specific; unlimited calls); the verifier (four
  rubrics, `NO` is a checked violation and `UNKNOWN` a lead, never gives the answer, write the
  file first, ≤ 3 rounds); finishing (verifier before finishing, accepted only for the checked
  file, `task_complete` + empty commands ends at once, call + flag = call); the three-section
  answer file (`## Statute` section numbers, `## Reasoning`, `Answer:` last; quoted heredoc)
  and arithmetic in `python3`. The grader is unchanged.

## 7. Logs per trial

Under `<trial>/agent/`:

- `reader/call-N.json` (query, parsed result, validated excerpts with the extracted text, the
  rendered result, commands and outputs, steps, parse failures, tokens, seconds),
  `reader/call-N.packet.txt`, `trajectory.reader-call-N.json` (ATIF, linked from the solver's
  trajectory as a subagent reference).
- `rubric_verifier/round-N.json`, `round-N.packet.txt`, `trajectory.verifier-round-N.json` as
  in run-3; each round also records the answer hash it verified.
- `rubric_verifier/summary.json`: all run-3 fields plus `reader_calls` (count, steps, chars,
  tokens, seconds per call), `claims` (every `task_complete`: hash verified or not, outcome),
  `finish_reason` (`verified` / `budget_exhausted` / `unverified_accepted` / `not_finished`),
  `solver_rule_file_reads` (solver commands that name the rule file), `decisions` after each
  report (continue / finish / sub-agent call), and the usual token totals including both
  sub-agents.

## 8. Settings for the first run (owner's decisions 2026-10-28)

| Setting | Value | Note |
|---|---|---|
| Base | run-3 agent (t2fix, index packet, section-number record, no confidence) | `report_confidence=false` stays |
| Model / sampling / serving | Qwen3.5-35B-A3B, T 0.7, thinking on for all three roles, 16k output per call, prefix caching on, `--n-concurrent 2` | as run-3 |
| `max_verify_rounds` / `verifier_max_steps` | 3 / 4 | as run-3 |
| `reader_max_steps` / per-command output / `reader_max_chars` | 3 / 6,000 bytes / 16,000 chars | |
| Reader calls per trial | unlimited, counted | wall clock is the limit |
| `max_unverified_claims` | 2 | then accept, record `unverified_accepted` |
| Solver reads of the rule file | allowed, counted | no interception |
| Wall clock | 600 s × 3 = 1,800 s, sub-agent time included | as before; 600 s re-scoring reported alongside |
| Controls | none; one draw on the 65 SARA tasks | owner: no ablations, no second draw |
| Skills | solver v1, reader v1, verifier v4 (four rubrics), all hand-written | one skill per role |

Job: `jobs/qwen3.5-35b-a3b/t2rv-direct/`. Smoke test first on tax-case-93 and s2-b-2-c-pos
(the two tasks of the run-2/3 smoke tests), then the 65-task run with no code or skill change in
between.

What the smoke test must show before the full run (plan §4): reader text appears verbatim in
the solver's tool response and the reader's own commands do not; truncation is marked; the
solver writes the three-section file and calls the verifier; the verifier reads the actual file;
an unverified `task_complete` is bounced and a verified one ends the trial without a second
confirmation; the verifier counts reader-delivered text as coverage and still finds an omitted
section when there is one.

## 9. Runs

### 9.1 `rv-smoke1` (2026-10-28, aborted)

`jobs/qwen3.5-35b-a3b/t2rv-direct/rv-smoke1/`, tasks tax-case-93 and s2-b-2-c-pos, the §6 prompt
(stock template + solver skill v1), reader v1, verifier v4. Killed after ~7 min by Claude Code's
memory-pressure reaper (host at ~1 GB free with vLLM at 0.75 utilisation plus two task
containers; see [02](02_setup_and_sync.md) §2b), while both trials were inside verifier round 1.
Partial trajectories are kept. What the 7 minutes showed (✅ from the trajectories):

- The first prompt carries the skill (6.9k chars). Both solvers nevertheless opened with
  `cat /app/statute.txt` (10,000-byte truncated observation), i.e. read the file themselves before
  anything else.
- Binary trial: step 2 put the reader call into a **shell keystroke**
  (`subagent={"name": "reader", ...}`); step 3 corrected itself ("I need to use the proper JSON
  response format with a subagent field") and called the reader properly. Reader call 1: 2 command
  steps, 72 s, 7.2k prompt / 1.3k completion tokens, returned §2(b) lines 204–236 and 231–234
  (the second range is inside the first; the skill says "do not return text the solver already
  has" only across calls). Step 4 wrote the answer file; the verifier was running when killed.
- Numeric trial: never called the reader. Step 3 started writing `/app/query_reader.py`
  (`import requests`), then searched the statute with `python3 -c` regexes over `open(...).read()`
  for three steps, wrote the answer file at step 6 and claimed `task_complete` at step 7 with no
  verifier round; the gate bounced it with the expected message ("Unverified claim 1 of 2"); the
  verifier round the solver then requested was running when killed.
- Harness mechanics that were exercised worked: skill substitution, reader call → verbatim
  extraction → `READER RESULT` block, completion gate bounce, trajectory upload for the verifier.
  Not yet exercised: a rendered verifier report with the four rubrics, a verified finish.

🔶 The two misuses (reader call as a keystroke; a self-made Python "reader") suggest the solver
does not register the `subagent` field from the skill alone; whether to make the field more
prominent in the prompt is the owner's call before the next smoke.
