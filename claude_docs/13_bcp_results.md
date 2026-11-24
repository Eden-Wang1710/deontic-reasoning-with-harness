# 13 · BrowseComp-Plus results (Qwen3.5-9B zero-shot)

**Status (2026-11-23, 13:30 UTC).** Three runs on `dev50` are finished and judged: the baseline
(the released CLM agent) at **28.0%** and the solver-reader-verifier-compaction BCP-harness v0.1
at **54.0%**, for 3.5 times the compute. The baseline is now run and judged on all 830
questions: **24.0%** (199/830) at 3.23 PFLOPs per question (§6). **v0.2 (2026-11-23, §10)**, whose reader sees the first 512 tokens of a document and opens more itself, has **52.0%** on `dev50` at 9.21 PFLOPs by v0.1's counting, 6.79 when a reader conversation's repeated prefix is counted once. This document holds every BrowseComp-Plus (BCP) result so far. The design, the
code and the reasons for each choice are in [12](12_bcp_harness_design.md); nothing here
repeats them beyond what is needed to read the numbers. ✅ marks a number read from a run,
🔶 an estimate or a reading.

## 1. Result

| `dev50`, Qwen3.5-9B zero-shot | Baseline (`clm-base-dev50`) | v0.1 (`bcpsrvc-dev50`) | Change |
| --- | ---: | ---: | ---: |
| **Accuracy** ✅ | 28.0% (14/50) | **54.0%** (27/50) | +26.0 points |
| Wilson 95% interval | 17.5 to 41.7 | 40.4 to 67.0 | |
| Questions with an answer | 14 | 30 | |
| Correct among the answered | 14/14 | 27/30 | |
| **Compute per question, PFLOPs** ✅ | 3.20 | 11.32 | 3.5 times |
| Compute per correct answer, PFLOPs | 11.4 | 21.0 | 1.8 times |
| Cost per question, US cents (§4) | 7.67 | 29.88 | 3.9 times |

- **Paired** ✅: both right 13, v0.1 only 14, baseline only 1, both wrong 22. Exact McNemar
  test p = 0.001.
- **Against the CLM paper** (arXiv 2609.37725v1, Table 2, all 830 questions): zero-shot 9B
  28.8% at 1.52 PFLOPs; after RL training 42.5% at 1.34. On the same 830 questions our
  baseline has 24.0% (interval 21.2 to 27.0) at 3.23 PFLOPs (§6): 4.8 points under the paper
  and twice its compute. `dev50` alone (28.0%) looked like the paper's accuracy; the other 780
  questions are at 23.7%. 🔶 The reason for either gap is not found; the paper runs up to
  80 turns and does not give its top-k and snippet length for that table.
- **v0.2** (`bcpsrvc2-dev50`, §10): 52.0% (26/50), against v0.1 both right 22, v0.1 only 5, v0.2 only 4; 9.21 PFLOPs per question counted as in this table.
- One run each, at temperature 0.7. On 50 questions a single accuracy has an interval of
  about ±13 points; the paired difference of 26 points is well outside what one run's noise
  explains, the exact size of the gain is not.

## 2. Setup of the two runs

Both runs: Qwen3.5-9B served by vLLM on spark-0238, context budget 28,672 tokens (26,624
enforced), 100 steps, temperature 0.7, 16 trials at once; retriever Qwen3-Embedding-8B with
the pre-built index, exact search, top 10; judge Qwen3.5-35B-A3B at temperature 0 with the
template of BCP's `evaluate_run.py`; a question without an answer file counts as wrong.
`dev50` is the first 50 questions of one fixed random order of the 830 (`bcp/query_order.json`).

| | Baseline | v0.1 |
| --- | --- | --- |
| Agent | `ClmAgent` as released, `configs/bcp.yaml` | the same class with three commands replaced (`bcp/bcp_srvc.py`) |
| What a search returns to the solver | 10 snippets, the first 512 tokens of each document | 10 cards: grade, line ranges and labels from a reader that read each new document (up to 8,192 tokens); the text of the picked lines for new strong and weak documents, at most 2,500 tokens |
| Reading a document | `get_document`: the first 8,192 tokens into the solver's context | `read_document <docid> "<what to look for>" [from_line]`: the reader reads it again, the solver gets the picked lines |
| Before submitting | nothing | `verify`: one verifier call on the answer and the lines it cites; a submit without it is refused, at most twice |
| Compaction | CLM's: the model edits its context file | the same |
| Run time | 2 h 20 min + 10 min judge | 6 h 45 min + 15 min judge |

## 3. Where the gain comes from

**Reading** 🔶: most of the gain that can be traced comes from the solver no longer dying at
the context limit, because a search result is 8 times smaller. Reading deep into documents
explains 2 of the 14 questions gained. The solver's searching did not get better. The
verifier, the fallback answer and the state table contributed nothing measurable.

### 3.1 How the trials ended ✅

| | Baseline: trials | correct | v0.1: trials | correct |
| --- | ---: | ---: | ---: | ---: |
| Submit | 9 | 9 | 20 | 20 |
| Context limit, six retries used up | 37 | 5 | 1 | 0 |
| Step limit (100) | 4 | 0 | 29 | 7 |

- **Baseline.** The CLM harness never compacts by itself. When the context passes the limit it
  rolls back the newest turns and asks the model to condense its context file, up to six
  times, then gives one last turn. All 37 trials that ended this way used all six retries;
  **30 of them made no context edit at all**, the other 7 made 40 edits together. The model
  went on searching after each notice. (The CLM paper reports the same of the untrained 9B on
  TerminalBench: 1.4 edits per task, none in half of the tasks.)
- **v0.1.** 0.58 rollbacks per trial against 5.3. The trials that do not find an answer now
  run to the step limit instead.

### 3.2 What a command puts into the solver's context ✅

| Mean characters returned | Baseline | v0.1 |
| --- | ---: | ---: |
| One `search` | 19,364 (about 4,700 tokens) | 2,311 (about 560 tokens) |
| One `get_document` / `read_document` | 8,113 | 785 |

With a limit of 26,624 tokens the baseline holds four or five search results; v0.1 holds
several dozen.

### 3.3 The 14 questions gained ✅ (classification 🔶)

All 14 ended on the context limit in the baseline, without an answer file. Classified by
looking for the reference answer, as a string, in the retrieved documents and in everything a
command printed to the solver (`bcp/analysis/funnel.py`):

| What happened in the baseline | Questions | Ids |
| --- | ---: | --- |
| The answer had been printed to the solver in a snippet; the trial hit the limit before answering | 6 | 1007, 1217, 418, 53, 540, 595 |
| The answer lies beyond the first 512 tokens of its document; only the reader reached it | 2 | 442, 843 |
| No retrieved document held the answer; v0.1 retrieved one, with the answer in its first 512 tokens | 3 | 1040, 1098, 1208 |
| No string match in either run (the documents paraphrase the answer) | 3 | 68, 793, 852 |

The one question lost (728) had all its evidence retrieved and passed on in v0.1, and ran to
the step limit without an answer file.

### 3.4 Retrieval did not change ✅

| | Baseline | v0.1 |
| --- | ---: | ---: |
| Gold recall after the first 19 searches of a trial | 53.0% | 59.0% |
| Search at which the first gold document comes back, median | 2 | 2 |
| Answer string in a retrieved document / printed to the solver / answered / correct | 28 / 23 / 14 / 14 | 31 / 28 / 30 / 27 |
| Evidence recall over the whole trial (19 and 45.6 searches) | 49.5% | 63.0% |

The solver writes queries no better than before. What changed is what happens after
retrieval: of the 23 baseline trials that had the answer on the screen, 11 answered.

### 3.5 The parts of v0.1 ✅

- **Reader.** 151 calls per question, none failed (7,533 calls). It read 133 distinct
  documents per question, every new document once, and passed on text for 18 of them (8.3
  strong, 10.0 weak). `read_document` was called 17.8 times per question on 5.7 documents.
- **Verifier.** Called in 25 trials: 13 ACCEPT, 11 REVISE, 1 report without a verdict. All 25
  answers were judged correct, so **no REVISE stopped a wrong answer**. After a REVISE the
  answer file was rewritten in all 11 trials; 6 then submitted, 5 ran to the step limit and
  called `verify` again up to 13 times, getting the stored report. 🔶 Whether the answer or
  only its explanation changed is not checked. In 23 of the 25 trials the first verifier call
  (with thinking) was cut at 4,096 tokens before a verdict; the report came from the second
  call, without thinking.
- **Gate.** Never refused a submit: all 20 submits came after a verifier round.
- **Fallback answer.** Never used. 20 trials ended without an answer file; 6 of them never
  wrote the state table, 14 did, and none had a Best answer the command could read. The final
  state table is not saved outside the sandbox, so it is unknown whether the part was empty
  or in another form. To fix before the next run.
- **State table.** Written 1.4 times per trial after the template; 15 trials never wrote it.
- **Wrong answers.** The only 3 are from the 6 trials that wrote an answer file and ran out
  of steps before `verify` (3 right, 3 wrong).

### 3.6 Limits of this reading 🔶

- One run each; the three questions "retrieved only by v0.1" can be the chance of different
  queries.
- The string match is crude and misses paraphrased answers.
- The reader makes the result smaller and reads deeper at once; these two are not separated.
  A control would be v0.1 with a reader that sees only the first 512 tokens of a document.
  Not run.

### 3.7 The failures, by how the trial ended and by the gold documents ✅

A gold document is a document of `qrel_golds.txt` (it holds the final answer; every `dev50`
question has 1 to 6). "Returned": a search of the trial returned one. "Reached the solver":
in the baseline the solver opened one with `get_document`; in v0.1 the reader graded one
strong or weak, so its lines were on a card. Counted from `<job>.tools.jsonl`.

| | Baseline | v0.1 |
|---|---|---|
| Wrong, of 50 | 36 | 23 |
| ended at the context limit (six requests to condense, then the end) | 32 | 1 |
| ended at the 100-call limit | 4 | 22 |
| submitted a wrong answer | 0 | 0 |

The failures at the 100-call limit:

| | Baseline (4) | v0.1 (22) |
|---|---|---|
| No search ever returned a gold document | 3 | 12 |
| Returned, but it did not reach the solver | 1 (never opened) | 2 (reader graded it none) |
| A gold document reached the solver | 0 | 8 |
| … no answer file written | | 6 (q561, q610, q728, q806, q1103, q1110) |
| … answer file written, judged wrong | | 2 (q110, q774) |

- The 12 of v0.1 without a gold document made 75 searches on average. The third wrong answer
  file of v0.1 (q873) is among them.
- In the 8 where a gold document reached the solver it did so early: at search 1, 1, 1, 2,
  3, 6, 20 and 23, of 49 to 81 searches. In 5 of the 8 the solver also called
  `read_document` on a gold document (5 to 9 times). None of the 8 called `verify`.
- In the 2 where the reader graded every gold document none, the first one was returned at
  search 57 and 47.
- The baseline's 32 failures at the context limit: a search returned a gold document in 17,
  the solver opened one in 9; in 15 none was returned (16 searches on average before the end).
- So of v0.1's 23 failures, 13 never had a gold document (12 and the one at the context
  limit), 2 are the reader's, and 8 had one in front of the solver and did not end with a
  correct answer.

## 4. Cost at list prices

Same method as on DeonticBench (`tools/inference_cost.py`, decision of 2026-11-11 in
[04](04_initial_proposal.md)): tokens of a trial times a price card, once without a cache and
once with a prompt cache simulated inside the trial. `bcp/analysis/cost.py`, numbers in
`outputs/bcp/dev50_cost.json`. The judge is not priced.

| US cents per question, mean of `dev50` | Baseline | v0.1 | Ratio |
| --- | ---: | ---: | ---: |
| Qwen3.5-9B's own OpenRouter price (0.17 input / 0.25 output USD per million, fetched 2026-11-22) ✅ | 7.67 | 29.88 | 3.9 |
| Thesis price card (0.14 / 1.00 / 0.05 cache read), no cache ✅ | 6.80 | 27.38 | 4.0 |
| Thesis price card, with cache 🔶 | 4.62 | 17.70 | 3.8 |
| For comparison: PFLOPs | 3.20 | 11.32 | 3.5 |

- The OpenRouter listing of `qwen/qwen3.5-9b` has **no cache-read price**
  (`tools/pricing/price_card_qwen3.5-9b.json`), so with its own card a cache saves nothing.
  The cache line uses the thesis card, which is the price of Qwen3.5-35B-A3B.
- Whole `dev50` at the 9B's own price: 3.84 USD for the baseline, 14.94 USD for v0.1.
- **In dollars the solver is the larger part of v0.1, in PFLOPs the reader is.** Without a
  cache the solver is 17.9 of the 29.9 cents: 78 calls, each sending about 13,100 tokens
  again. In PFLOPs the reader is 85%, because CLM's formula counts a cached prefix as free
  for the solver, and `bcp/report.py` counts every reader token as uncached. 🔶 With the
  reader's estimated cache the total would be about 9.4 PFLOPs, 2.9 times the baseline.
- **How the cache is simulated.** Solver: CLM's own replay of the prompts through a prefix
  cache; 55% of the baseline's input tokens and 91% of v0.1's are cached. Reader 🔶: the
  prompts are not stored, so the cached part is estimated as the reader skill, the question
  and an unchanged state table, about 20% of its input.

## 5. Size of the trajectories, against SARA

Means per task. The SARA rows are Qwen3.5-35B-A3B on SARA numeric (35 tasks, runs of
[10](10_solver_checks_harness.md) §7 and [11](11_srvc_harness_design.md)); the BCP rows are
Qwen3.5-9B on `dev50`. Cents are on the thesis price card for all rows, so they compare token
volumes, not what each model would cost.

| | Model calls | Input tokens | Output tokens | Documents read | Cents, no cache / with cache |
| --- | ---: | ---: | ---: | --- | ---: |
| SARA numeric, terminus-2 baseline | 5.8 | 30,216 | 13,261 | one statute, 6,097 tokens | 1.75 / 1.54 |
| SARA numeric, solver-reader-verifier-compaction v0.1 | 14.7 | 196,008 | 32,632 | the same | 6.01 / 4.74 |
| SARA numeric, solver-checks | 21.6 | 283,590 | 54,303 | the same | 9.40 / 7.20 |
| BCP, baseline | 26.9 | 442,415 | 6,079 | 58 documents as 512-token snippets, 2.2 opened | 6.80 / 4.62 |
| BCP, v0.1 | 230 (solver 78, reader 151, verifier 1) | 1,706,216 | 34,957 | 133 documents read by the reader, about 4,500 tokens per call | 27.38 / 17.70 |

- A BCP trajectory is much longer than a SARA one, and the length is input: the BCP baseline
  reads 15 times the input of the SARA baseline; v0.1 on BCP reads 8.7 times the input of the
  same harness family on SARA with about the same output (35.0K against 32.6K).
- The cost structure is the reverse. A SARA call writes about 2,300 tokens, so output is most
  of the cost. A BCP baseline call writes about 230 tokens; the cost is the context sent
  again and the documents read.
- In v0.1 the solver's input is 1,028K tokens and the reader's 676K. A solver prompt is
  13.1K tokens on average (baseline 16.5K), with 2.9 times as many calls.

## 6. Baseline on all 830 questions ✅

Three jobs with the same settings: `clm-base-dev50` (50), `clm-base-rest780` (715) and
`clm-base-rest780b` (65). Judged with the same judge and template as `dev50`; no judge reply
without a verdict.

| Baseline, Qwen3.5-9B zero-shot | `dev50` | other 780 | **all 830** |
| --- | ---: | ---: | ---: |
| Correct | 14 | 185 | **199** |
| **Accuracy** | 28.0% | 23.7% | **24.0%** |
| Wilson 95% interval | 17.5 to 41.7 | 20.9 to 26.8 | 21.2 to 27.0 |
| Questions with an answer file | 14 | 202 | 216 (26%) |
| Correct among the answered | 14/14 | 185/202 | 199/216 (92%) |
| **Compute per question, PFLOPs** | 3.20 | 3.23 | **3.23** |
| Model calls per question | 26.9 | 27.1 | 27.1 |
| Searches per question | 19.0 | 19.5 | 19.5 |
| Evidence documents returned by a search | 49.5% | 48.4% | 48.5% |
| Gold documents returned by a search | 55.0% | 48.9% | 49.2% |

How the 830 trials ended:

| End | Trials | Correct | Wrong answer | No answer file |
| --- | ---: | ---: | ---: | ---: |
| Context limit (six requests to condense, then the end) | 605 (73%) | 42 | 8 | 555 |
| Submit | 168 (20%) | 154 | 5 | 9 |
| 100-call limit | 51 (6%) | 3 | 4 | 44 |
| Exception | 6 | 0 | 0 | 6 |

- `dev50` is not harder or easier than the rest beyond chance: 28.0% lies inside what 50
  questions drawn from a 24% population give (interval ±13 points).
- The picture of §3.1 holds on the full set: 73% of the trials end at the context limit and
  92% of those leave no answer.
- The 6 exceptions: 5 times a command passed the 120 s limit, once the agent time limit.
  They count as wrong.
- For the paper's table: the baseline of the full benchmark is 24.0%, not `dev50`'s 28.0%.
  v0.1 has no number on the 780 yet.

How the 780 were run:

**`clm-base-rest780`: 715 of 780 trials, stopped.** ✅ Started 2026-11-21 13:40 UTC, right
after `bcpsrvc-dev50`, about 28 trials per hour. Stopped on 2026-11-22 at 14:51 UTC: the
machine (119 GB) ran out of memory and the run was killed from outside; the run script
stopped both servers. 185 of the 715 have an answer file (26%; `dev50`: 28%). 5 ended on an
exception: 4 on a command that passed the 120 s limit, 1 on the agent time limit. No tool
call failed. 16 trials were in progress and are lost (their directories are kept in
`jobs/clm-base-rest780.interrupted/`), 49 had not started.

**What took the memory** 🔶. One `python3` process of user root, 32.3 GB, started about
14:49:48 UTC and gone when the sandbox containers were removed. Nobody else was logged in;
our two servers run as the normal user; commands inside a sandbox run as root, and the
sandbox had no memory limit. So it was a command the solver wrote in one of the 16 running
trials. The command itself is not stored (an interrupted trial writes no trajectory). Four
trials were inside a command at that moment (q301, q408, q1167, q513); q301 fits best: its
previous step ended at 14:49:50 with the context at 25,530 of 26,624 tokens, the point where
the CLM agent asks the model to condense its context file. The model does that with Python:
262 of the 719 trials with a step log ran `python3` (2,410 of 37,147 commands), mostly
regular-expression rewrites of the context file, the slowest finished one took 86 s; and
all 4 trials that ended on the 120 s command limit ended on the command that followed that
request.

**Change: the sandbox has a memory limit.** ✅ `bcp/build_tasks.py` now writes
`memory_mb = 4096` into every task (`--memory-mb`, 0 = none); Harbor turns it into the
container's `mem_limit`. A command that passes it is killed and the trial goes on. Tasks
built before this (both `dev50` runs and the 715 trials) had no limit; a limit changes only
trials in which a command would use more than 4 GB.

**`clm-base-rest780b`: the other 65 questions.** ✅ Task set `rest780b` =
`rest780` without the questions that have a result in `clm-base-rest780`
(`build_tasks.py tasks --set rest780 --without-job … --name rest780b`). 2026-11-22 18:02 to
20:21 UTC, 16 concurrent trials, same settings, sandbox limited to 4 GB. 15 of 65 correct
(23.1%; the 715: 23.8%). One exception (a command over 120 s); no tool call failed; no
command output shows a kill by the memory limit. Then the judge graded both jobs and the
servers were stopped (21:33 UTC).

## 7. Earlier runs, not results

| Run | What it was | Outcome |
| --- | --- | --- |
| Retrieval acceptance test | our index and query encoding against the BCP paper's Table 2 | reproduced (12 §11.1) |
| `clm-base-smoke` | baseline, 5 questions | 0 of 5 answer files; 3 ended on the context limit, 2 on the step limit (12 §11.3) |
| Reader evaluation | reader prompt variants on 20 questions outside `dev150` | as a relevance filter the reader passed on 34 to 36% of the gold documents; as an extractor with a grade, 81% (12 §12.1) |
| `bcpsrvc-smoke` | v0.1, 5 questions, stopped after 53 to 80 calls per trial | reader path works; verifier and submit not reached (12 §12.3) |

A defect in `clm-base-dev50` ✅: 10 of about 1,000 tool calls returned an HTTP 500 (the
tokenizer was called from two threads), one each in 10 trials; the command printed a request
to try again. Three of those trials are correct. Fixed before every later run; the run was
not repeated.

## 8. Open

1. The control of §3.6 (reader limited to the first 512 tokens), to separate the smaller
   result from the deeper reading.
2. The verifier: no wrong answer stopped, its thinking call cut in 23 of 25 trials.
3. The fallback answer and the state table: save the final state table outside the sandbox,
   then find why no Best answer was read.
4. Cost: the 29 trials that run to the step limit cost 14.8 PFLOPs each and 7 are correct;
   the 20 that submit cost 6.4 and all are correct.
5. The gaps between our baseline and the paper on all 830 questions: 24.0% against 28.8%,
   3.23 PFLOPs against 1.52.
6. v0.2 is run (§10; the reader summary planned in 12 §8 was replaced by the owner on
   2026-11-23). Open after it: which rule counts the reader's compute (§10.1); the trials
   that run to the step limit, 78% of v0.2's compute; the `none` grades given from the
   first window without an action (§10.2); a v0.1 or v0.2 number outside `dev50`.

## 9. Files

| | |
| --- | --- |
| `outputs/bcp/clm-base-dev50/`, `outputs/bcp/bcpsrvc-dev50/`, `outputs/bcp/clm-base-rest780/`, `outputs/bcp/clm-base-rest780b/` | `scores.json` (judge) and `report.json` (per question: end, calls, tokens, PFLOPs, recall), ids and numbers only |
| `outputs/bcp/bcpsrvc2-dev50/` | the same two files for v0.2 (§10) |
| `outputs/bcp/dev50_cost.json`, `outputs/bcp/dev50_funnel.json` | §4 and §3; `dev50_cost.json` now also holds `bcpsrvc2-dev50` |
| `outputs/bcp/dev50_shared_prefix.json`, `bcp/analysis/shared_prefix.py` | §10.1, rule 3: the reader's compute with the shared prefix counted once |
| `outputs/bcp/dev50_failures.json`, `outputs/bcp/dev50_funnel_v01_v02.json` | §3.7 and §10: failures by end and by gold documents, paired tables; the answer funnel of v0.1 against v0.2 |
| `bcp/report.py`, `bcp/judge.py` | per-question report and judging |
| `bcp/analysis/cost.py`, `bcp/analysis/funnel.py`, `bcp/analysis/failures.py` | §4, §5, §3, and §3.7 with §10.4 |
| `tools/pricing/price_card_qwen3.5-9b.json` | the 9B's list price with the raw response |
| `~/data/browsecomp-plus/jobs/<job>/` on 0238 | trajectories, tool logs and judge outputs; they hold question and answer text and stay out of the repository |

## 10. v0.2 on `dev50` (`bcpsrvc2-dev50`, 2026-11-23) ✅

v0.2 changes the reader only ([12](12_bcp_harness_design.md) §13): it sees the first 512
tokens of a document, grades it, and may ask up to three times for more (`OPEN` a line
range, `FIND` phrases). Same 50 questions, model, retriever, judge, 100 steps and 16 trials
at a time as `bcpsrvc-dev50`. Two differences besides the reader: every sandbox has the
4 GB memory limit of §6 (no command was killed by it), and the solver skill's two sentences
on what the reader does are reworded. Run 06:28 to 12:19 UTC, then judged; no error in
the tools log, no failed reader call (v0.1: 7), no trial exception.

| `dev50`, Qwen3.5-9B zero-shot | Baseline | v0.1 | **v0.2** |
| --- | ---: | ---: | ---: |
| **Accuracy** ✅ | 28.0% (14/50) | 54.0% (27/50) | **52.0%** (26/50) |
| Wilson 95% interval | 17.5 to 41.7 | 40.4 to 67.0 | 38.5 to 65.2 |
| Questions with an answer | 14 | 30 | 33 |
| Correct among the answered | 14/14 | 27/30 | 26/33 |
| **PFLOPs per question**, every role prompt token counted (the rule of §1) ✅ | 3.20 | 11.32 | **9.21** |
| … a reader conversation's repeated prefix counted once ✅ | 3.20 | 11.32 | **6.79** |
| … and the prefix that the reader calls of a trial share counted once (§10.1) | 3.20 | 9.56 | **3.87** |
| US cents per question, the 9B's own price, no cache (§4) | 7.67 | 29.88 | 28.11 |
| US cents per question, thesis card with cache 🔶 | 4.62 | 17.70 | 13.89 |

- **Paired with v0.1** ✅: both right 22, v0.1 only 5 (q61, q418, q442, q1098, q1208), v0.2
  only 4 (q610, q703, q728, q806), both wrong 19. Exact McNemar test p = 1.0.
- **Paired with the baseline** ✅: both right 13, v0.2 only 13, baseline only 1 (q61), both
  wrong 23. p = 0.002.
- 🔶 One run at temperature 0.7 on 50 questions. The two points between v0.1 and v0.2 are
  far inside the noise; the run shows no loss of accuracy and cannot exclude one of several
  points in either direction.

### 10.1 Compute, under three ways of counting

The solver's compute is CLM's cache-aware count in every row (1.68 PFLOPs per question in
v0.1, 1.79 in v0.2). The rows differ in how the reader's prompt tokens are counted.

| PFLOPs per question | v0.1 | v0.2 | Change | v0.2 against the baseline (3.20) |
| --- | ---: | ---: | ---: | ---: |
| 1. Every prompt token of every reader call ✅ | 11.32 | 9.21 | -19% | 2.9 times |
| 2. The turns of one document's conversation reuse its prefix ✅ | 11.32 | 6.79 | -40% | 2.1 times |
| 3. As 2, and the reader calls of a trial reuse the prefix they share (skill, question, unchanged state table) | 9.56 | 3.87 | -60% | 1.2 times |
| Per correct answer, rule 1 / 2 / 3 | 21.0 / 21.0 / 17.7 | 17.7 / 13.1 / 7.4 | | baseline 11.4 |

- **Rule 1** is what §1 and `12` report for v0.1. Under it v0.2 saves little: the v0.2
  reader makes 1.43 calls per document and each later call sends the conversation again.
- **Rule 2** is exact: the log records for every later turn how many prompt tokens repeat
  the conversation so far (`cached_prompt_tokens`). v0.1's reader has one turn per
  document, so its number does not change.
- **Rule 3** ✅ (2026-11-23, `bcp/analysis/shared_prefix.py`,
  `outputs/bcp/dev50_shared_prefix.json`): the shared text is rebuilt and tokenized with the
  model's tokenizer and chat template. Skill and question are exact; so is the state table
  while it is the empty template. For a state table the solver has written the log holds
  only its length, so that part is estimated 🔶; it is 0.15 PFLOPs in either run. A prefix
  counts as reused when an earlier reader call of the trial had it, a replay in order, which
  is the assumption of CLM's formula for the solver.

  | PFLOPs per question, rule 3 | v0.1 | v0.2 |
  | --- | ---: | ---: |
  | Replay in order, with the estimated state tables (the number used above) | 9.56 | 3.87 |
  | Replay in order, exact part only | 9.71 | 4.03 |
  | Reused only if the earlier call had ended before this one started, with the estimated state tables | 9.66 | 4.03 |
  | Reused only if the earlier call had ended, exact part only | 9.81 | 4.18 |

  The last two rows allow for the reader calls of one search running in parallel. So rule 3
  gives 3.9 to 4.2 PFLOPs for v0.2 and 9.6 to 9.8 for v0.1. The first estimate from
  character counts (3.72 and 9.41, `bcp/analysis/cost.py`) was a little low, because
  document text has more tokens per character than the skill.
- The shared prefix is about 1,280 tokens per document, 70% of the v0.2 reader's first
  prompt (the reader skill alone is about 1,000 tokens), and 19% of the v0.1 reader's
  prompt. The model server does reuse such a prefix (vLLM prefix caching was on in both
  runs); whether the thesis counts it is the owner's choice.
- Under rule 3 the reader (2.03) costs about what the solver does (1.79); the verifier is
  0.05.
- **At list prices** the gain is small without a cache (28.1 against 29.9 cents), because
  there the solver's re-sent context is the larger part (18.8 cents). With the thesis card's
  cache price it is 13.9 against 17.7 cents.
- **Time** ✅: the run took 5.8 hours against 6.7; the reader calls sum to 56 hours against
  81; a solver call waited 28 seconds on average against 49.

### 10.2 The reader in the run ✅

| Per question, mean | v0.1 | v0.2 |
| --- | ---: | ---: |
| Documents read by the reader | 150.7 | 163.6 |
| Reader calls | 150.7 | 233.6 |
| Reader tokens per document (rule 2) | 4,598 | 2,178 |
| Gold documents: graded strong or weak at their first reading | 100 of 105 (95.2%) | 95 of 104 (91.3%) |
| Recall of gold documents, returned by a search / passed on to the solver | 0.661 / 0.645 | 0.632 / 0.576 |
| Recall of evidence documents, returned / passed on | 0.630 / 0.581 | 0.606 / 0.536 |
| Questions where the answer string was retrieved / shown to the solver (`funnel.py`) | 31 / 28 | 31 / 28 |

- **8,182 documents read** (7,045 new ones at searches, the rest through `read_document`):
  `none` 6,418 (78%), `weak` 820, `strong` 944.
- **Actions:** none on 6,574 documents (80%); one on 520, two on 288, three on 800.
  2,656 `FIND` and 840 `OPEN`. The reader acts where it found something: on 87.5% of the
  documents it grades strong, 82.2% of the weak ones, **1.7% of the `none` ones**.
- **It read 5.6% of the lines** of the documents it was given. 11% of the documents fit
  into the first window.
- **Where the tokens go** (rule 2): 1,905 per `none` document, 3,027 per weak, 3,292 per
  strong; the `none` documents take 69% of the reader's tokens (v0.1: 87% of its input).
  Most of those 1,905 tokens are the shared prefix, which is why rule 3 changes so much.
- **The loss at the reader:** 9 gold documents were graded `none` at their first reading
  (v0.1: 5). In all 9 the reader took no action: it judged from the first window and
  stopped. In 2 questions (q61, q1110) no other gold document was passed on and the
  question is wrong; q61 is right in the baseline and in v0.1. The offline check
  (`12` §13.2) had 90.0% of gold documents passed on against 91.7%; the run has 91.3%
  against 95.2%.
- The solver called `read_document` more often (22.8 against 17.8 times per question) and
  with a start line more often (158 against 62 calls).

### 10.3 The solver side ✅

| | v0.1 | v0.2 |
| --- | ---: | ---: |
| Model calls of the solver, per question | 78.4 | 84.1 |
| Searches per question | 45.6 | 48.3 |
| Ended with `submit` (all correct in both runs) | 20 | 17 |
| Ended at the 100-call limit: correct / wrong answer / no answer | 7 / 3 / 19 | 9 / 7 / 17 |
| Ended at the context limit | 1 | 0 |
| Trials that called `verify`; verdicts ACCEPT / REVISE | 25; 13 / 11 | 25; 7 / 18 |
| PFLOPs per trial (rule 1), ended at the limit / submitted | 14.8 / 6.4 | 10.9 / 5.9 |

- The 33 trials at the limit are 78% of v0.2's compute (v0.1: 76%).
- **More wrong answers: 7 against 3.** All 7 are answer files left behind at the 100-call
  limit; 5 of these trials never called `verify`, in 2 the last verdict was REVISE. No
  submitted answer is wrong, as in v0.1.
- The verifier said REVISE more often (18 of 25 against 11 of 25; 🔶 Fisher's exact test
  about p = 0.15, so not established). 10 of the 18 trials stored an answer again and
  submitted it; all 10 are correct. Its thinking call was cut at 4,096 tokens in all 25
  trials (§8 item 2 is unchanged).

### 10.4 The failures, as in §3.7 ✅

`bcp/analysis/failures.py`, `outputs/bcp/dev50_failures.json`. All 24 failures of v0.2
ended at the 100-call limit.

| Wrong trials | Baseline (36) | v0.1 (23) | v0.2 (24) |
| --- | ---: | ---: | ---: |
| No search ever returned a gold document | 18 | 13 | 14 |
| Returned, but it did not reach the solver | 9 (never opened) | 2 (reader: `none`) | 2 (reader: `none`; q61, q1110) |
| A gold document reached the solver | 9 | 8 | 8 |
| … no answer file | 9 | 6 | 4 (q110, q561, q832, q1098) |
| … answer file, judged wrong | 0 | 2 | 4 (q418, q542, q774, q802) |

- The 14 without a gold document made 71 searches on average (v0.1: 72).
- In the 8 where a gold document reached the solver it did so at search 1, 2, 3, 15, 15,
  16, 21 and 26, of 37 to 82. One of the 8 called `verify`.
- **The 9 questions that changed sides against v0.1.** Lost: q442 and q1208 (no gold
  document returned in v0.2), q61 (the reader graded the gold document `none`), q418 and
  q1098 (a gold document reached the solver; wrong answer, no answer). Gained: q610, q728
  and q806 (in v0.1 a gold document had reached the solver without an answer), q703 (v0.1
  never retrieved one). So one of the five losses is the v0.2 reader's; the others are in
  the parts that v0.2 did not change.

### 10.5 Reading 🔶

- v0.2 keeps v0.1's accuracy on `dev50` as far as 50 questions can tell, and reads 5.6% of
  the documents' lines instead of up to 8,192 tokens of each.
- How much cheaper it is depends on the counting rule: 19%, 40% or 60%. Under rule 3 the
  harness costs 1.2 times the baseline for 52% against 28%; under rule 1, 2.9 times. The
  rule has to be fixed before a number goes into a table.
- The shape of the failures did not change: retrieval never finds a gold document in 14
  questions, the reader loses 2, and in 8 the solver has a gold document on a card and does
  not end with a correct answer. The trials that run to the limit remain the cost: 33
  trials, 78% of the compute, 9 correct.
- The reader's remaining weakness is specific: a `none` from the first window without
  looking further. Nothing in the reader skill asks for a FIND before a `none`.
- Not held out: the reader skill was tuned on 120 questions outside `dev150`, the harness
  was developed on `dev50`. Neither v0.1 nor v0.2 has a number outside `dev50`.
