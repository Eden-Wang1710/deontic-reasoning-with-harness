# 13 · BrowseComp-Plus results (Qwen3.5-9B zero-shot)

**Status (2026-11-22, 02:34 UTC).** Two runs on `dev50` are finished and judged: the baseline
(the released CLM agent) at **28.0%** and the solver-reader-verifier-compaction BCP-harness v0.1
at **54.0%**, for 3.5 times the compute. The baseline on the other 780 questions is running
(370 of 780). This document holds every BrowseComp-Plus (BCP) result so far. The design, the
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
  28.8% at 1.52 PFLOPs; after RL training 42.5% at 1.34. Our baseline has the paper's accuracy
  and twice its compute. 🔶 The reason for the compute gap is not found; the paper runs up to
  80 turns and does not give its top-k and snippet length for that table.
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

## 6. Baseline on the other 780 questions (`clm-base-rest780`) — running

Started 2026-11-21 13:40 UTC, right after `bcpsrvc-dev50`; with `dev50` it gives the baseline
on all 830 questions. At 02:34 UTC on 2026-11-22: **370 of 780 trials finished**, about 28 per
hour, so about 15 more hours; the judge runs after that and the servers are stopped. 89 of
the 370 have an answer file (24%; `dev50`: 28%). Not judged yet. No tool call has failed; one
trial ended on a command that passed the 120 s limit.

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
5. The gap between our baseline's 3.20 PFLOPs and the paper's 1.52.
6. v0.2 (reader summary instead of excerpts), as planned in 12 §8.

## 9. Files

| | |
| --- | --- |
| `outputs/bcp/clm-base-dev50/`, `outputs/bcp/bcpsrvc-dev50/` | `scores.json` (judge) and `report.json` (per question: end, calls, tokens, PFLOPs, recall), ids and numbers only |
| `outputs/bcp/dev50_cost.json`, `outputs/bcp/dev50_funnel.json` | §4 and §3 |
| `bcp/report.py`, `bcp/judge.py` | per-question report and judging |
| `bcp/analysis/cost.py`, `bcp/analysis/funnel.py` | §4, §5 and §3 |
| `tools/pricing/price_card_qwen3.5-9b.json` | the 9B's list price with the raw response |
| `~/data/browsecomp-plus/jobs/<job>/` on 0238 | trajectories, tool logs and judge outputs; they hold question and answer text and stay out of the repository |
