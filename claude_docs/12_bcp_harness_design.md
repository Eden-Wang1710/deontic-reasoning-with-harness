# 12 · Solver-reader-verifier-compaction BCP-harness v0.1: baseline and design

**Status (2026-11-21): the baseline is built and run on `dev50` (28.0%, §11.4). v0.1 (§5) is
implemented (§12) and run on `dev50` (54.0% at 11.3 PFLOPs per question, §12.4); the
baseline on the remaining 780 questions (`clm-base-rest780`) is running.** All results, with the cost at list prices and the analysis of the gain, are collected in [13](13_bcp_results.md). §1 records what the owner decided on 2026-11-20 and
2026-11-21. Everything else is proposed; the points that need the owner's word are collected in
§10. Facts marked ✅ were read in the papers or in the two repositories
(BrowseComp-Plus at commit `0469490`, CLM at commit `3c89408`); 🔶 marks an estimate or a
hypothesis.

The harness of `11` goes to a second benchmark, BrowseComp-Plus (BCP), and to a third base
agent, the CLM agent. Two things are built: a **baseline** (the CLM agent as released, on a BCP
task we rebuild) and the **solver-reader-verifier-compaction BCP-harness v0.1** (the same agent
loop with a reader and a verifier added).

## 1. Decisions that fix the frame (owner, 2026-11-20 and 2026-11-21)

- **Model: Qwen3.5-9B, zero-shot.** The RL checkpoint of the CLM paper is not released.
- **The solver is the main loop of `ClmAgent`.**
- **Compaction is CLM's**: the model edits the mirror file of its context. Our compaction role
  (state page, harness rebuild, `11` §3.3) is not used here.
- **The reader has a context of its own and no harness window.** Its work never enters the
  solver's context; only its result does. This departs from the 2026-11-19 decision that the
  reader is a branch of the solver's context (`11` §1): a solver held to a 26.6K window cannot
  carry the reader's searches.
- **The reader returns only what is useful, in two versions to compare**: verbatim excerpts, or
  the reader's own summary.
- **Development runs on a subset.** 2026-11-21: 150 questions are too many for development
  (35 SARA numeric cases already take 6 hours). The development set is `dev50`, and its size
  is fixed after the smoke run has measured the time per question (§3.5).
- **A baseline with the CLM agent's own harness is run first on that subset**, as close to the
  paper as the released material allows. It does not have to be identical. 2026-11-21: the
  baseline is brought up first; the construction of v0.1 (§5) is discussed further after it.
- **Retriever: Qwen3-Embedding-8B. Judge: Qwen3.5-35B-A3B from disk** (2026-11-21).
- **Name**: *solver-reader-verifier-compaction BCP-harness v0.1*. Identifier in code and logs:
  `bcpsrvc` (harness field `bcpsrvc-v0.1`).

## 2. What exists and what does not ✅

**BrowseComp-Plus** (`paper`: arXiv 2508.06600; repo `texttron/BrowseComp-Plus`, MIT):

- 830 questions, a fixed corpus of 100,195 web documents (mean 5,179 words). Per question 6.1
  evidence documents (needed to answer) and 2.9 gold documents (needed and containing the
  answer); `topics-qrels/qrel_evidence.txt` and `qrel_golds.txt`.
- Questions, answers and relevance labels are distributed encrypted, with a canary that says
  the data must never appear online as plain text. The corpus is not encrypted.
- Pre-built indexes on Hugging Face (`Tevatron/browsecomp-plus-indexes`): BM25 and
  Qwen3-Embedding 0.6B / 4B / 8B.
- Grading prompt `GRADER_TEMPLATE` in `search_agent/prompts.py`; the answer format is
  Explanation / Exact Answer / Confidence.

**CLM** (arXiv 2609.37725v1; repo `facebookresearch/context-language-models`, CC BY-NC 4.0):

| Part | State |
| --- | --- |
| `ClmAgent` (a Harbor `BaseAgent`, harbor 0.16.1; one `bash` tool; own loop) | released |
| `configs/bcp.yaml` (28,672-token budget, 2,048 reserve, 4,096 output tokens, T 0.7, top-p 0.95, shrink edit gate, 6 rollback retries) | released |
| The BCP Harbor task, the search tools (mounted as a skill), the judge script | not in the repository |
| The retriever | not named in the paper; "top 10 snippets of 512 tokens", document reader capped at 8,192 tokens (App. E) |
| The summary-harness baseline, the RL checkpoints | not in the repository |

Numbers of the paper for Qwen3.5-9B zero-shot on all 830 questions: CLM 39.9%, summary harness
37.7% (App. F, "32K context limit"); and CLM 28.8%, summary 34.7% (Table 2, before RL, 28K
budget and 80 turns). The paper does not reconcile the two, and gives the budget as 32K in
§5.1.1 and 23,560 in App. E. **Our baseline number is therefore our own; 28.8 to 39.9% is the
range it is compared with.**

How `ClmAgent` works, as far as the design depends on it:

- Tools reach the model as **executables on `PATH` in the sandbox**, described by a `SKILL.md`
  appended to the system prompt (`utils/skills.py`). The loop rejects any tool call other than
  `bash`.
- The run ends when the model runs `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
- The system prompt and the task are pinned; every later turn is in the mirror file and can be
  edited. A turn that only edits the mirror is free of the step count.
- Cost is reported as FLOPs with prefix-cache reuse (`flops_metrics`, model key `9b`).

## 3. Shared setup (both arms)

### 3.1 Model and serving

- Qwen3.5-9B, bf16, vLLM 0.20.1 of `myenv`, thinking on, prefix caching on. One server for the
  solver, the reader and the rubrics. The model is not on 0238 yet; 19.3 GB of weights ✅.
- `--max-model-len` at least 65,536: the solver needs 32,768, the reader more (§5.3).
- 🔶 To check on the first start: the tool-call parser for this model (`qwen3_xml` is what
  our 35B server uses; the CLM README names `qwen3_coder`).
- Sampling as in `bcp.yaml`: T 0.7, top-p 0.95, 4,096 output tokens per call.
- The server is started for a run and stopped when the run ends (rule of this machine).

### 3.2 Corpus and retrieval

- Retriever: **Qwen3-Embedding-8B with the pre-built BCP index** (owner, 2026-11-21), exact
  inner-product search and query encoding on the GPU (16.4 GB together ✅). It is the strongest retriever of the BCP
  paper and its main setting. The CLM paper does not say what it used.
- A **tools server** on the host (our code, one process) holds the index, the embedding model
  and the corpus. It is written against `torch` and `transformers` directly (a matrix product
  over the 100,195 normalised vectors gives the same ranking as BCP's flat FAISS index)
  rather than through Tevatron, to keep `flash-attn` and Pyserini (Java 21) off this ARM
  machine. The limits (10 results, 512 and 8,192 tokens) are set on the server; a request
  carries only the query or the docid. Every call is appended to a log the agent cannot edit.
- **Acceptance test before any agent run**: with the full question as the query, the server
  must reproduce Table 2 of the BCP paper for this retriever (evidence documents: Recall@5
  14.5, Recall@100 47.7, nDCG@10 20.3) within rounding. ✅ Passed, §11.1.
- **Line addressing.** The server renders every document as numbered lines (the document's own
  lines, wrapped at 300 characters). A piece of the corpus is addressed as *docid, lines a-b*
  everywhere: search snippets, the reader's pointers, the solver's direct lookups, the
  verifier's windows.

### 3.3 Task

- One Harbor task per question, built by a script from the decrypted data into a directory
  outside the repository (`~/data/browsecomp-plus/tasks/`). The sandbox is one small image
  with Python (`bcp-sandbox:1`). Its network is Harbor's allowlist mode with the Docker bridge
  address as the only host, so the sandbox reaches the tools server and nothing else: the
  open web is not a source. The agent's model calls are made from the host.
- Instruction: the BCP query prompt (question, cite documents by docid) plus where to write
  the final response (`/app/answer.md`) and the submit command.
- `tests/` only stores the answer file. Grading runs after the job, because the judge is a
  different model.

### 3.4 Judge

- The grading template of BCP's `scripts_evaluation/evaluate_run.py` (the one that repository
  uses with an open-weight judge; it differs from `GRADER_TEMPLATE` in `prompts.py` by
  accepting string variations of the correct answer), T 0, thinking on, verdict parsed as
  that script does. A missing answer file counts as wrong and costs no call. The judge sees
  the same three fields in both arms (Explanation, Exact Answer, Confidence).
- Judge model: **Qwen3.5-35B-A3B**, which is on disk (owner, 2026-11-21). The CLM paper used
  Qwen3.5-27B, BCP's repository now uses Qwen3-32B. One judge for every row of our table is
  what matters for the comparison.

### 3.5 Development subset: `dev50`, inside a fixed order

- **One order, three sets.** The 830 query ids are shuffled once with a fixed seed and the
  order is committed (ids are not encrypted in the release). `smoke5` is its first 5,
  `dev50` its first 50, `dev150` its first 150. A larger set always contains the smaller
  one, so results carry over when a set is enlarged.
- **`dev50` is the development set** (owner, 2026-11-21: 150 is too many). **The number is
  fixed after `clm-base-smoke`**: the largest of 30, 50 or 80 for which one run stays under
  about 6 hours on 0238.
- **What 50 questions can show** 🔶. At an accuracy near 35% the 95% interval of one run is
  about ±13 points (±7.6 at 150). For two arms compared question by question, only a
  difference of roughly 20 points is reliably distinguishable (roughly 11 at 150; assuming a
  quarter of the questions change outcome between two runs at T 0.7). `dev50` therefore
  serves to see that a harness runs as designed and to read where it fails, with the process
  measures of §7 (evidence recall, quotes found, what the verifier catches). It does not
  rank two configurations that are close.
- **`dev150` is kept for one confirmation**: the baseline and the best v0.1 configuration,
  once, before anything is said about accuracy. The baseline needs only the 100 questions
  that `dev50` does not cover.
- **Compute** 🔶, not measured for this model:
  - Table 2 of the CLM paper gives 1.52 PFLOPs per question for the zero-shot 9B CLM agent.
    With the released formula (2 × 6.92e9 × tokens for the linear part) that is at most
    about 110,000 tokens per question that miss the cache, input and output together. The
    output is a part of it, so the baseline should be lighter than a SARA case under `11`
    (about 40,000 output tokens per case, from 6 hours for 35 cases at about 65 tokens/s).
  - A 9B model leaves memory for more parallel trials than the 35B server had (it ran
    `--max-num-seqs 8`, 4.7 busy on average). The smoke run tries 16.
  - `clm-base-smoke` replaces these estimates with tokens, seconds and throughput per
    question.

### 3.6 Data rule

The decrypted questions and answers never go into git, and neither does any file that contains
them: task directories, trajectories, answer files, judge outputs. The repository is public.
Committed outputs carry query ids, scores and counts only.

## 4. The baseline: `clm-base`

`ClmAgent` unmodified, installed from the CLM repository at a pinned commit into an
environment of its own (it pins harbor 0.16.1). Its code is not copied into this repository.

| Setting | Value | Source |
| --- | --- | --- |
| Config | `configs/bcp.yaml` | released |
| Context budget | 28,672 tokens, reserve 2,048 (enforced limit 26,624) | `bcp.yaml` |
| Task steps | 100; editing turns are free | paper (Fig. 5, App. E); `bcp.yaml` ships 500 |
| FLOPs model key | `9b` | `bcp.yaml` ships `27b` |
| Tools | skill `bcp_search_v1`: `search "<query>"` (top 10, 512 tokens each) and `get_document <docid>` (8,192 tokens) | App. E; our rebuild |
| Retriever, judge, task | §3.2 to §3.4 | ours |
| `command_timeout` | 120 s, as released | v0.1 needs more for a reader call (1,800 s); the baseline's commands return within a second, so the longer limit would only make a hung command cost more time |
| Concurrency | 16 trials, vLLM `--max-num-seqs 16`, 50% of memory | measured, §11.2 |

Runs: `clm-base-smoke` (`smoke5`), then `clm-base-dev50`.

## 5. The BCP-harness v0.1 (confirmed by the owner, 2026-11-21)

    solver (ClmAgent loop, 26.6K window, edits its own context, keeps a state table on disk)
       |  search "<query>"     read_document <docid> "<what to look for>"     verify
       v
    tools server
       |-- search: top 10; every document new to this question is read by the reader,
       |           one call per document, in parallel; the solver receives excerpts
       |-- read:   one reader call on one document, with the solver's request
       |-- verify: one verifier call, one round

**Principle (owner): the harness carries text between the roles and does not judge.** It
retrieves, numbers lines, copies the lines a role points to, attaches the state table to a
role's prompt and counts tokens. Whether a citation is real, whether it supports a claim and
whether an answer holds is decided by a model role. The evidence is recorded by the solver,
not collected by the harness.

### 5.1 How the roles are attached

The CLM agent takes its tools as commands in the sandbox, so **the roles are commands too**:
`search`, `read_document` and `verify` are small executables of the skill `bcp_solver_v1`. Each one
posts to the tools server, which runs the role and prints the result. The solver's loop is
the released one. A thin subclass (`BcpSRVC(ClmAgent)`) adds the completion gate and the
fallback answer (§5.5).

This is a third way of attaching the harness: fields of a JSON reply on terminus-2, native
tools on KIRA, sandbox commands here.

### 5.2 Solver, state table, compaction

- The solver searches itself. `get_document` and the raw 512-token snippets are gone: what
  comes back from a search is what the reader took from the documents.
- **State table**, a file the solver keeps (`/app/state.md`), five parts: **Goal** (the
  question as a list of conditions), **Found** (fact, `[docid:lines]`), **Conclusions**
  (candidates, what is ruled out), **Next** (what to look for), **Best answer** (the three
  lines of the answer format, updated whenever the solver has a better one). The solver
  writes its queries from Next.
- The harness does not write the table, does not remind and does not enforce. How often the
  solver updates it is counted and reported.
- Compaction is CLM's, untouched: budget, nudges, rollback and edit gate of `bcp.yaml`. CLM
  pins only the system prompt and the task, so the state table is what survives an edit of
  the context.
- The answer goes to `/app/answer.md` in the baseline's format (Explanation, Exact Answer,
  Confidence).

### 5.3 Reader

What the reader is for (owner): a search returns 10 documents of about 7,400 tokens each.
Showing the first 512 tokens of each and letting the solver decide what to open asks it to
judge a document by its head; opening everything fills and pollutes its context. The reader
reads the documents and passes on only what is useful.

Measured on the baseline's call logs (39 questions, 555 searches, 2026-11-21) ✅:

| | |
| --- | --- |
| Document length, tokens | median 2,340, mean 7,400, 17% above 8,192 |
| Results that are new to the question | 40% (10 on the first search, 3.2 on a later one) |
| Reader tokens per search, every new document once, cap 8,192 | about 11,500 |
| The same, all 10 results every time | about 33,000 |
| Into the solver's context per search in the baseline | about 5,000 |
| Answer string in a gold document: first occurrence within 512 / 2,000 / 8,192 tokens | 82% / 93% / 98% (of the 86% of gold documents that contain it verbatim) |

- **Who chooses what is read: nobody.** Every document of the top 10 that this question has
  not seen is read, once, up to 8,192 tokens. A document seen before is not read again; its
  stored result is shown. This costs about as much as letting the reader choose from the
  snippets (which are 5,000 tokens themselves) and leaves no choice to be made from a
  document's head.
- **One call per document, in parallel.** Context of a call: the reader skill, the question,
  the solver's state table (the file as it is, attached by the harness), the query, and one
  document with numbered lines. The document comes last, so the calls of one search share a
  cached prefix. The reader reads for every condition of the question, not only for the
  query, because its result is kept for the rest of the trial.
- **Reply (v0.1, excerpts):** a grade of its own (match strong, weak or none) and, unless
  none, line ranges with a one-line label. The harness copies the lines verbatim. The reader
  extracts; it is not asked to filter (§12.1).
- **What the solver receives:** one line per result (docid, title, how far it was read);
  under a relevant document its excerpts. About 600 to 1,500 tokens per search instead of
  5,000 🔶.
- **`read_document <docid> "<what to look for>" [from_line]`** (not `read`, which is a shell
  builtin) has one document read again with the
  solver's own request: when the solver has learned something that makes an old result
  stale, or to continue a document longer than 8,192 tokens from the line where the reader
  stopped.
- **v0.2** replaces the reply by the reader's own summary; everything else stays.

### 5.4 Verifier

One model call, one round, in a context of its own. The solver calls `verify` when the
answer file is written.

- **Input:** the question, the answer file, the state table, and the text of the lines the
  state table and the answer cite as `[docid:lines]`, each with a few lines around it. The
  harness looks these lines up and passes them on; a citation that points nowhere is passed
  on as such.
- **Output:** one table, a row per condition of the question (supported by which citation,
  not supported, or contradicted), then whether the rows concern one entity and whether the
  answer follows from them, then ACCEPT or REVISE with what is missing.
- The three rubrics of `11` are one table here: coverage and support are two columns of the
  same row, grounded reasoning is the closing judgement. No check is made by the harness.
- A second `verify` returns the stored report without a model call.

🔶 On this benchmark the verifier is not where the cost is: one call of 6,000 to 8,000
tokens, against about 11,500 reader tokens for every search.

### 5.5 Completion gate and fallback answer

- A submit before a verifier round is not executed: the solver is told to run `verify`.
  After `max_unverified_claims` (2) such attempts the submit is accepted and the trial is
  marked `unverified_accepted`. The rule of `11` §9.
- **Fallback answer (owner, 2026-11-21, as on SARA):** if the run ends without an answer
  file and the state table has a Best answer, the harness writes the answer file from it.
  The trial is marked `answer_source=state_table`; such an answer has not been through the
  verifier. Results are reported with and without these trials.

### 5.6 Where v0.1 differs from `11`

| | `11` (DAR, terminus-2 and KIRA) | BCP v0.1 |
| --- | --- | --- |
| Reader is called | by the solver, with a query | by every search, on each new document |
| Reader's context | a branch of the solver's context | its own, one document per call |
| Survey and outline | yes | none |
| Compaction | harness window, state page, rebuild | CLM file editing, unchanged |
| State | state page written by the compaction role | state table kept by the solver |
| Pinned after compaction | first message, state page, answer file, report | system prompt and task |
| Verifier | three rubrics, harness checks of shape and quotes | one call, one table, no harness check |
| Verifier's source | the whole rule document in the prefix | the cited lines |
| Attachment | reply field, native tool | sandbox command |

## 6. Harness, skills, code

- **Tools server** (host): retrieval, line addressing, the reader calls and their stored
  results per question, the verifier call, the log of every call with its tokens per role.
- **Skills**: `bcp_search_v1` (baseline tools), `bcp_solver_v1` (solver text and the three
  commands), `reader_excerpt_bcp_v1` (v0.2: `reader_summary_bcp_v1`), `verifier_bcp_v1`.
- **Agent**: `bcp/bcp_srvc.py`, `BcpSRVC(ClmAgent)`, gate and fallback answer only.
- **Layout**: a top-level `bcp/`. Data, task directories and jobs are under
  `~/data/browsecomp-plus/`, outside the repository (§11).

## 7. What is measured

- **Accuracy** (judge) with a Wilson interval, and the paired table against the baseline
  (right in both, only in one, in neither).
- **Calibration error** of the stated Confidence, as BCP reports it.
- **Evidence recall and gold recall**: the documents the tools returned, and for v0.1 also
  the documents from which the reader passed on an excerpt.
- **Citations**: precision and recall of the cited docids against the evidence labels.
- **Cost per question, per role**: FLOPs with prefix-cache reuse (CLM's formula, so the
  number is comparable with theirs) and US cents with cache on a price card for this model.
  🔶 A price card for Qwen3.5-9B does not exist in `tools/pricing/` yet.
- **Context**: largest solver prompt, edits of the mirror, rollbacks, task steps, model calls.
- **Reader**: calls per question, tokens read against tokens delivered, share of documents
  judged relevant.
- **State table**: number of versions per question, whether a Best answer was there at the end.
- **Verifier**: ACCEPT and REVISE on right and on wrong answers, whether the answer changed
  after the report, finish reasons, answers taken from the state table.

A search is one solver step but several reader calls, so equal step limits are not equal
compute. The comparison of cost is made on FLOPs and cents, not on steps.

## 8. Runs, in order

1. Setup: environment, model and index downloads, tools server, acceptance test (§3.2), task
   builder, judge script.
2. `clm-base-smoke`, then `clm-base-dev50`. Compared with the paper's range (§2), as far
   as 50 questions allow (§3.5).
3. v0.1 implemented; `bcpsrvc-smoke`.
4. `bcpsrvc-dev50`. v0.2 (summary) after it.
5. If v0.1 differs from the baseline: two more runs that separate the roles, the baseline
   with the verifier only and the baseline with the reader only. Without them a difference
   cannot be assigned to a role.
6. `dev150` for the baseline and the better v0.1 mode (§3.5); the full 830 only if the
   result is to be reported next to the papers' numbers.

## 9. Risks 🔶

- **Protocol load on a 9B model.** Before RL the paper's 9B is worse with context editing
  than with the summary harness (Table 2). In the baseline it left the context unedited in
  most trials (§11.3). v0.1 asks it to keep a state table as well, and nothing enforces it. The 35B model already drifted out of its role in a branch (`11` §9).
- **The reader as the only path** can lose a passage the solver would have recognised in raw
  results, and a result stored early is read against an early state table. On SARA about 9 wrong answers came from provisions the reader never delivered
  (`09` §10.11). The recall numbers of §7 are there to see it.
- **Summary mode** puts the reader's wording into the solver's context. The quote keeps a
  finding tied to the text, but a wrong reading of a correct quote passes the harness.
- **The baseline is a reconstruction.** Task text, retriever and judge are ours. A number
  outside 28.8 to 39.9% is not by itself an error, but it has to be explained before v0.1 is
  compared with it.
- **Memory.** 9B model, embedding model and index together fit on 0238 (119 GB) with room 🔶;
  the judge runs after the agent run on a server of its own.

## 10. Confirmed, and still open

- ✅ 2026-11-21, owner: retriever Qwen3-Embedding-8B; judge Qwen3.5-35B-A3B; baseline first;
  `dev50`; the v0.1 of §5 (reader reads every new document once up to 8,192 tokens; `read`
  for a second reading; state table kept by the solver; one verifier call; the harness does
  not judge; fallback answer from the state table).
- Chosen by Claude within that frame, for the owner to review: the state table is attached to
  every reader call; the verifier's source lines are looked up by the harness from the
  citations instead of the verifier opening documents itself; the gate of `11` is kept; no
  reminder or limit for the state table; the direct lookup `read_doc` is dropped.
- Open: the two role-separating runs of §8 step 5; whether the baseline's distance from the
  paper (§11.3) has to be explained first.

## 11. The baseline as built, and its smoke run (2026-11-21) ✅

Code in `bcp/`: `prepare_data.py` (index pickles read with a numpy-only unpickler, corpus
store, decryption), `tools_server.py` (`serve`, `eval`), `build_tasks.py`,
`query_order.json` (seed 20261121), `skills/bcp_search_v1/`, `sandbox/Dockerfile`,
`servers.sh`, `run_clm_base.sh`, `judge.py`, `report.py`. Environments: `~/envs/clm` (CLM
at commit `3c89408`, harbor 0.16.1), `~/envs/bcpprep` (pyarrow), `myenv` for the two
servers. Everything that holds plain text is under `~/data/browsecomp-plus/` (prepared
data, tasks, jobs, judge outputs), outside the repository.

### 11.1 Retrieval acceptance test

Full question as the query, 830 questions, evidence documents:

| Query prefix | Recall@5 | Recall@100 | Recall@1000 | nDCG@10 |
| --- | --- | --- | --- | --- |
| BCP paper, Table 2 | 14.5 | 47.7 | 76.7 | 20.3 |
| ours, `\n` written as two characters (the shell command in BCP's `qwen3-embed.md`) | 14.54 | 47.78 | 76.74 | 20.24 |
| ours, a real newline (BCP's `faiss_searcher.py`, which serves the agents) | 14.91 | 48.64 | 77.35 | 20.76 |

The paper's table is reproduced with the first prefix. The tools server uses the second,
because that is what BCP's own search tool gives an agent.

### 11.2 Serving

Qwen3.5-9B on vLLM 0.20.1 with `qwen3_xml` (its chat template is the 35B model's):
12.4 output tokens/s for one request, 180 tokens/s for 16 at once (11.2 each). With
contexts of 20K tokens a call takes about 20 s at 5 concurrent trials, most of it prefill.

### 11.3 `clm-base-smoke` (`smoke5`, 100 steps)

| | |
| --- | --- |
| Answer files | 0 of 5 |
| Ended on the context limit | 3 (13, 14 and 16 model calls; 0, 0 and 1 context edits; all 6 rollback retries used) |
| Ended on the step limit | 2 (100 and 102 model calls; 19 and 16 context edits) |
| Mean per question | 49 model calls, 36 searches, 3.4 documents opened, 10,157 output tokens, 5.06 PFLOPs |
| Evidence recall of everything the tools returned | 12.5% (gold 13.3%) |

- In three trials the model never edits its context. It searches until the limit, is rolled
  back, is told to condense, and searches again; after the sixth retry it gets a final turn
  and uses it for one more search.
- In two trials it edits the context regularly (from about 22K to about 2K tokens each
  time) and runs to the step limit. Told three times that the session is ending, it goes on
  verifying a candidate and writes no answer file.
- 🔶 Five questions do not contradict the paper's 28.8% (at that accuracy five failures in
  a row have a probability near 18%). But the mean cost is 3.3 times the paper's 1.52
  PFLOPs, which suggests that the paper's runs ended earlier, with an answer, more often
  than ours. `clm-base-dev50` decides whether this is a difference of our rebuilt task.
- The harness never condenses for the model. At the limit it discards the newest turns and
  asks for a compaction, at most `max_num_retry_on_limit` (6) times.

### 11.4 `clm-base-dev50` ✅

Qwen3.5-9B, `dev50`, 100 steps, 16 trials at once; judge Qwen3.5-35B-A3B. Numbers per question
in `outputs/bcp/clm-base-dev50/` (ids and numbers only).

| | |
| --- | --- |
| **Accuracy** | **14 of 50, 28.0%** (95% interval 17.5 to 41.7). Paper, zero-shot 9B, Table 2: 28.8% |
| Answer files | 14; all 14 judged correct (read against the gold answers: 13 clear, 1 lenient: a shorter form of the gold answer) |
| No answer | 36 |
| Mean stated confidence of the 14 | 98% |
| Cost | 3.20 PFLOPs per question (paper 1.52, at 80 turns); 27 model calls, 19 searches, 6,079 output tokens |
| Evidence recall of what the tools returned | 49.5% (gold 55.0%) |
| Wall-clock | about 2 h 20 min for the agent run, 10 min for the judge |

| How the trial ended | Trials | Answer files | Correct | Model calls | Context edits | PFLOPs | Evidence recall |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Submit | 9 | 9 | 9 | 20.1 | 1.9 | 1.84 | 93% |
| Context limit, retries used up | 37 | 5 | 5 | 20.6 | 1.1 | 2.97 | 43% |
| Step limit | 4 | 0 | 0 | 100 | 17 | 8.44 | 10% |

- The accuracy is the paper's. The fear after the smoke run (no answer in 5 of 5) was a
  matter of five questions.
- The baseline answers only when it is sure: every answer is right, 72% of the questions get
  none. Its errors are all of one kind, a missing answer. Of the 36 questions without an
  answer, 32 ran out of context retries.
- **A defect of our tools server in this run:** 10 of about 1,000 tool calls returned an
  HTTP 500 (the tokenizer was called from two threads), one each in 10 trials; the command
  printed "the request failed, try again" and the trial went on. Three of those trials are
  correct. Fixed before any later run (§12); the run was not repeated.

## 12. v0.1 as built (2026-11-21) ✅

Code: `bcp/roles_v01.py` (reader, verifier, gate, run by the tools server with `--roles`),
`bcp/bcp_srvc.py` (`BcpSRVC(ClmAgent)`), `bcp/skills/bcp_solver_v1/` (solver text and the
commands), `reader_excerpt_bcp_v1`, `verifier_bcp_v1`, `bcp/run_bcpsrvc.sh`. The task text
differs from the baseline's in the names of the commands only.

Settings: reader one call per document, no thinking, at most 600 output tokens, at most 4
ranges and 8 lines per document, 2,500 tokens of excerpt text per search result; verifier
with thinking, 4,096 output tokens, the cited lines with 3 lines around them (8,000 tokens
at most), report 1,500 tokens; role calls at T 0.7, top-p 0.95; `command_timeout` 1,800 s;
vLLM `--max-num-seqs 32`; everything else as the baseline.

### 12.1 The reader was measured before the run, and changed twice

On 20 questions outside `dev150` (positions 151 to 170 of the order): their gold documents,
up to 3 other evidence documents, and the first 4 retrieved documents that are not evidence.
The query is the question itself, the state table is empty.

| Reader | Gold documents passed on | Evidence | Retrieved, not evidence | Answer string in the excerpt or title | Output tokens per call |
| --- | --- | --- | --- | --- | --- |
| Filter (relevant yes or no), thinking, 2,000 tokens (8 questions) | 36% | – | 6% | 15% | 1,078; 43 of 54 first calls cut at the limit |
| Filter, no thinking, a short scan line first | 34% | 12% | 0% | 14% | 171 |
| **Extractor with a grade (strong, weak, none), no thinking, a scan line first** | **81%** (69 strong, 12 weak) | **63%** (51, 12) | 29% (15, 14) | **62%** | 158 |

- **Thinking is off for the reader.** With thinking a 9B reading call does not end within
  2,000 tokens in four cases of five, and a question needs about 100 such calls. The reply
  begins with one visible line of scanning instead. This departs from the kept-thinking rule
  of `11` §4.1 for this role.
- **Asked to filter, the reader throws away two gold documents of three**, with or without
  thinking. A single document rarely shows that it meets a multi-condition question. Asked to
  extract the best lines and grade them, it passes on 81%.

### 12.2 What the smoke runs changed

- A fast tokenizer was used from two threads (HTTP 500 on about 1% of the baseline's tool
  calls, §11.4). Query encoding and text measuring now have a tokenizer and a lock each.
- Harbor's egress proxy drops a connection that is silent for long. `search`,
  `read_document` and `verify` start a job on the tools server and poll for the result.
- A document shows the text of its lines in the search that had it read. When a later search
  returns it again it shows its line ranges and labels only. The solver repeats
  near-identical searches, and repeating the text filled its context with copies.

### 12.3 `bcpsrvc-smoke` (`smoke5`, stopped after 53 to 80 model calls per trial) 🔶

Not a result: the run was stopped to start `dev50`, and its server did not yet have the
third change above.

| | |
| --- | --- |
| Searches | 232; 3.9 new documents each; 0.57 strong and 0.74 weak per search |
| Reader calls | 954, 4,776 prompt and 102 output tokens on average, none without a usable reply |
| Solver context | grew to 22,500 to 24,300 tokens in every trial (the limit is 26,624) |
| State table | written 0, 0, 1, 2 and 3 times in the five trials |
| Verifier, submit | not reached |

- The reader path works: in one trial the solver had two of the entities the question asks for
  after three steps, from excerpts.
- The solver hardly keeps the state table, and it repeats the same search many times. Both
  were expected (§9) and neither is enforced or reminded (§5.2).
- **Cost.** A baseline trial usually dies at the context limit after about 20 calls. A v0.1
  trial does not, so it runs towards the 100 steps, with about 4 reader calls per search.
  A question will cost several times the baseline's 3.2 PFLOPs. `bcpsrvc-dev50` measures it.
- The gate, the verifier and the fallback answer were exercised through the sandbox
  commands in a container (refusal, report, accepted submit, answer file written from the
  state table) and at the end of a 6-step trial, not yet inside a full trial.

### 12.4 `bcpsrvc-dev50` ✅

Run on 2026-11-21, 16 trials at a time, 6 h 45 min for the agent run and 15 min for the
judge (Qwen3.5-35B-A3B, same template as §11.4). No tool call and no role call failed
(2,282 searches, 890 `read_document` calls, 7,533 reader calls, 48 verifier calls).
Summaries: `outputs/bcp/bcpsrvc-dev50/`.

| | Baseline (`clm-base-dev50`) | v0.1 (`bcpsrvc-dev50`) |
| --- | ---: | ---: |
| Correct | 14/50, 28.0% (17.5 to 41.7) | 27/50, 54.0% (40.4 to 67.0) |
| Questions with an answer | 14, all correct | 30, 27 correct |
| Cost per question, PFLOPs | 3.20 | 11.32 (solver 1.68, reader and verifier 9.65) |
| Model calls of the solver | 26.9 | 78.4 |
| Searches | 19 | 45.6 |
| Evidence recall of the retrieved documents | 49.5% | 63.0% |
| Ended on submit / context limit / step limit | 9 / 37 / 4 | 20 / 1 / 29 |

- **Paired.** Both right 13, v0.1 only 14, baseline only 1, both wrong 22; exact McNemar
  test p = 0.001. The one question lost (728) had all its evidence retrieved and passed on,
  and ran to the step limit without an answer file.
- **Cost.** 3.5 times the baseline per question, and 21.0 against 11.4 PFLOPs per correct
  answer. The reader is 85% of it: 151 calls and 693K tokens per question. The solver alone
  costs less than the baseline's (1.68 against 3.20), because its context grows slowly and it
  is seldom rolled back.
- **Where the cost goes.** The 20 trials that submit cost 6.4 PFLOPs and are all correct
  (evidence recall 94%). The 29 that run to the step limit cost 14.8 PFLOPs, 64 searches and
  199 reader calls each, and 7 of them are correct (evidence recall 43%).
- **Verifier.** Called in 25 trials: 13 ACCEPT, 11 REVISE, 1 report without a verdict. All
  25 answers were judged correct, so every REVISE was given to an answer that ended correct.
  After a REVISE the answer file was rewritten in all 11 trials; 6 then submitted and 5 ran
  to the step limit, calling `verify` again up to 13 times and getting the stored report.
  🔶 Whether the answer itself or only its explanation changed after a REVISE is not yet
  checked. In 23 of the 25 trials the verifier's first call (with thinking) was cut at 4,096
  tokens before a verdict, and the report came from the second call without thinking.
- **Gate.** No submit was refused: all 20 submits came after a verifier round.
- **Fallback answer.** Never used: 20 trials ended without an answer file and without a
  usable Best answer in the state table. The final state table is not saved outside the
  sandbox, so it cannot be told whether the part was empty or written in a form the
  command does not read. To fix before the next run.
- **Answers without a verifier round.** 6 trials wrote the answer file and ran out of steps
  before `verify`; 3 are correct and 3 wrong. These are the only wrong answers of the run.
- **State table.** Written 1.4 times per trial on average after the template; 15 of the 50
  trials never wrote it (6 of the 20 trials without an answer).

