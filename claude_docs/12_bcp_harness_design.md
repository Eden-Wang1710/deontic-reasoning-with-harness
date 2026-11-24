# 12 · Solver-reader-verifier-compaction BCP-harness v0.1: baseline and design

**Status (2026-11-21): the baseline is built and run on `dev50` (28.0%, §11.4). v0.1 (§5) is
implemented (§12) and run on `dev50` (54.0% at 11.3 PFLOPs per question, §12.4); the
baseline on all 830 questions is 24.0% (199/830) at 3.23 PFLOPs (`13` §6). v0.2 (2026-11-23, §13): the reader sees the first 512 tokens of a document and opens more itself; built, measured offline and run on `dev50` (`bcpsrvc2-dev50`: 52.0% at 9.21 PFLOPs by v0.1's counting, 6.79 with a reader conversation's repeated prefix counted once, §13.4).** All results, with the cost at list prices and the analysis of the gain, are collected in [13](13_bcp_results.md). §1 records what the owner decided on 2026-11-20 and
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


## 13. v0.2: the selective reader, which sees the beginning and opens more itself (2026-11-23)

**Owner's decision (2026-11-23).** v0.1's accuracy is wanted, its cost is not; v0.2 lowers
the cost at the reader, and a small loss of accuracy is accepted. The reader sees only the
first 512 tokens of a document and then decides itself whether to open more, how much and
which part. This replaces the v0.2 planned in §8 (a summary instead of excerpts), which
saves no reading. Everything else is v0.1: solver loop, state table, cards, `read_document`,
verifier, gate, compaction.

**Why the reader.** In `bcpsrvc-dev50` the reader is 9.65 of 11.32 PFLOPs per question, its
cost is input (676K prompt tokens against 17K output), and 87% of that input goes to
documents it then grades `none` (counted from the run's tool log). The baseline's solver decides from a 512-token
snippet whether to open a document, and pays for both in its own context; v0.2 moves that
decision into the reader's disposable context.

### 13.0 The selective reader, in one page ✅

**What it is.** In v0.2 the reader is a *selective reader*: it does not read a retrieved
document, it decides how much of it to read. It sees the beginning (512 tokens), grades the
document, and only when the beginning gives a reason does it ask the harness for more: a
line range (`OPEN`) or the lines that contain a phrase (`FIND`). The solver still never
sees a document; it gets a card with the title, the grade and the few lines the reader
picked, word for word.

| | Baseline (CLM agent) | v0.1 reader | v0.2 selective reader |
| --- | --- | --- | --- |
| Who decides whether a document is worth reading | the solver, from a 512-token snippet in its own context | nobody: every new document is read | the reader, from the first 512 tokens, in its own context |
| How much of a document is read | the snippet; the whole document when the solver opens it | up to 8,192 tokens of every new document | 512 tokens; more only on the reader's own `OPEN` / `FIND`, at most 3 times |
| What enters the solver's context per search | 10 snippets, about 19,400 characters | cards, about 2,300 characters | cards, about 2,400 characters |
| Model calls per document | none (the solver's own turns) | 1 | 1 to 4 turns of one conversation (1.43 on average) |
| Reader tokens per document (`dev50`) | | 4,598 | 2,178 |
| Share of the lines of the given documents that were read (`dev50`) | | | 5.6% |

**The flow of one `search`.**

```
solver (bash, in the sandbox)          tools server (host)                    reader (same 9B model, own context)
-----------------------------          -------------------                    ------------------------------------
search "<query>"  ───────────────────► retrieve top 10 (Qwen3-Embedding-8B)
  (sends /app/state.md along)          keep the documents not yet read in
                                       this trial; for each, in parallel:
                                         first 512 tokens, numbered lines ───► turn 0: SCAN / MATCH / LINES  [+ one action]
                                         OPEN a-b  -> those lines (≤2,000 tok) ◄── OPEN: 120-160
                                         FIND x; y -> lines containing x or y ◄── FIND: <phrase>; <phrase>
                                           with one line before and after
                                           (≤800 tok), other hits as numbers
                                         send the lines back ─────────────────► turn 1..3: SCAN / MATCH / LINES [+ action]
                                         no action line -> reading ends
                                       build one card per document:
                                         grade = highest grade given with lines
                                         lines = the LINES of all turns
                                                 (≤4 rows, ≤8 lines)
cards of the 10 documents ◄──────────  copy those lines from the corpus
```

**One reading, schematically** (placeholders, not benchmark text):

```
[user, turn 0]   # Question ... # State table of the solver ... # Request: The solver searched for: <query>
                 # Document <docid>: 310 lines in total. Shown: lines 1-14 (the beginning); 296 more lines are not shown.
                 1: <title> ... 14: <text>
[reader]         SCAN: Biography of <person>; lines 3-5 give birth year and profession, which fit condition 1. The award is not in the beginning.
                 MATCH: weak
                 LINES: 3-5 | <person>, born <year>, <profession>
                 FIND: <award name>; <year>
[user, turn 1]   2 line(s) contain <award name>; <year>.
                 187: <text>   188: <text with the phrase>   189: <text>
                 Reply in the same form: SCAN, MATCH, LINES for useful lines you have newly found, and an action only if you need one (2 left).
[reader]         SCAN: Line 188 states that <person> received <award> in <year>, which fits condition 2.
                 MATCH: strong
                 LINES: 188-188 | received <award> in <year>
```

The card the solver then gets for this document:

```
[3] docid <docid> | <title> | read lines 1-14, 187-189 of 310 | match: strong
  lines 188-188: received <award> in <year>
  188: <text of line 188, copied from the corpus>
  lines 3-5: <person>, born <year>, <profession>
  3: <text> ...
```

**Four properties that matter.**

1. *The decision is the reader's, the lookup is the harness's.* The harness numbers lines,
   returns the lines asked for and copies the lines pointed to. It does not rank, filter or
   judge. `FIND` is a substring lookup.
2. *Every turn carries a grade and lines, and the result accumulates.* A later turn cannot
   take back what an earlier one found. Without this the reader graded like a verifier
   after its own `FIND`s and the share of gold documents passed on fell from 92% to 68%
   (§13.2).
3. *Only lines that were shown can be passed on.* A `LINES` range is cut to what the
   reader has seen, so a card cannot contain text the reader did not read.
4. *The actions are turns of one conversation.* The prefix of each turn is what the model
   server has already processed, so a further look costs the new lines only (`13` §10.1 on
   how this is counted).

**What it costs and what it keeps** (`dev50`, `13` §10): 52.0% against v0.1's 54.0%
(paired 4 gained, 5 lost); 6.79 PFLOPs per question against 11.32 with a conversation's
repeated prefix counted once, 3.87 against 9.56 when the prefix shared by the reader calls
of a trial is counted once too; baseline 3.20. The reader takes no action on 80% of the
documents. Its weakness: 9 of 104 gold documents were graded `none` from the first window
without an action.

### 13.1 As built ✅

- **Code.** `bcp/roles_v02.py` (`Roles2`, a subclass of v0.1's `Roles`: it replaces
  `read_one`, the wording of the card's "read lines" part and the merging in
  `read_document`), the reader skill `bcp/skills/reader_open_bcp_v2/`, the solver skill
  `bcp/skills/bcp_solver_v2/` (the commands of `bcp_solver_v1`; only its text on what the
  reader does differs), `tools_server.py --reader-mode open`, and
  `VERSION=v02 bcp/run_bcpsrvc.sh <set> <job>`. v0.1 runs as before.
- **First prompt.** Reader skill, question, state table, request, then the header
  `Document <docid>: N lines in total. Shown: lines 1-k (the beginning); M more lines are
  not shown` and the first 512 tokens as numbered lines (`--first-tokens`).
- **Every reply** has a scratch line, a grade and lines (`SCAN`, `MATCH`, `LINES`, as in
  v0.1) and may end with one action:
  - `OPEN: a-b` shows these lines, at most 2,000 tokens per action (`--open-tokens`);
  - `FIND: phrase; phrase` shows every line that contains one of the phrases (substring,
    case ignored) with the line before and after, at most 800 tokens, and gives the line
    numbers of the matches that did not fit (`--find-tokens`).
  At most 3 actions per document (`--reader-actions`). A reply without an action ends the
  reading. Each action is one more turn of the same conversation, so the model server reuses
  the prefix it has processed.
- **The result of a document** is the highest grade that a reply gave together with valid
  lines, and the lines of all replies, the latest first, within v0.1's limits (4 rows, 8
  lines). A later reply does not take back what an earlier one found; `read_document` treats
  earlier readings the same way since v0.1. Only lines that were shown to the reader can be
  passed on; a range is cut where they end.
- **What the harness does** is what it did in v0.1: number lines, look lines up, copy the
  lines the reader points to, count tokens. FIND is a text lookup. What to look for, whether
  to open and what a line is worth are the reader's decisions.
- **`read_document`** uses the same reader: 512 tokens from line 1 or from `from_line`, then
  the reader's actions. The card says which line ranges were read (`read lines 1-18, 40-80
  of 155`).
- **Logged** per reader call: its turn and the prompt tokens that repeat the same
  conversation (`cached_prompt_tokens`); per document (`reader_doc`): actions, grade, lines
  seen. `bcp/report.py` gives the roles' compute by v0.1's rule (every prompt token counted)
  and with the repeated prefix of a conversation counted once
  (`role_pflops_conversation_cache`).
- **Also new:** every sandbox has a 4 GB memory limit (`13` §6); the task sets `val5` and
  `val20` (positions 150 and later of the fixed order, outside `dev150`).

### 13.2 The reader was measured before the run, and changed twice ✅

`bcp/analysis/reader_check.py`: 120 questions outside `dev150`; for each, the first search
of the baseline that returned a gold document gives that document and two documents of the
same result that are neither gold nor evidence (359 documents). Both readers read each
document with the empty state table. `outputs/bcp/reader_check_v02.json`.

| Reader | Gold passed on (strong or weak) | Answer string in the lines passed on (of 111 gold documents that hold it) | Other documents passed on | Tokens per document, gold / other |
| --- | ---: | ---: | ---: | ---: |
| v0.1 (up to 8,192 tokens) | 91.7% (102 + 8 of 120) | 77 | 19.7% | 3,355 / 4,630 |
| v0.2, first form | 68.3% | 55 | 5.4% | 2,577 / 2,432 |
| v0.2, second form | 70.0% | 64 | 7.1% | 2,613 / 2,351 |
| **v0.2, as run** | **90.0%** (94 + 14 of 120) | **81** | 25.5% | 2,817 / 2,211 |

Tokens: prompt and completion, a conversation's repeated prefix counted once. With every
prompt token counted, v0.2 as run is 6,121 / 3,578.

- **First form:** the reader either asked for more or gave its result. After its own FINDs
  it graded like a verifier: "the document does not mention the other conditions", so
  `none`, for documents that are the answer (the mineral's own article; the album's own
  article). It had seen the answer string in 102 of the 111 gold documents and passed it on
  in 55.
- **Second form:** a paragraph on grading added to the skill (one document covers one link
  of the chain; a FIND that finds nothing does not lower the grade). Nearly no change.
- **As run:** every reply carries a grade and lines, also the first one, and the result of
  a document accumulates (§13.1). The grade of the first reply is made as in v0.1, before
  the reader starts looking for the rest of the question.
- **What the actions bring:** the answer string is within the first 512 tokens in 86 of the
  111 gold documents, within what the v0.2 reader has seen at the end in 103, within v0.1's
  8,192-token window in 106.
- **How often it opens:** no action on 74% of the other documents and 34% of the gold ones.
- 🔶 One sample of documents, one draw at temperature 0.7, empty state table. The reader was
  changed against this sample, so the 90.0% is not a held-out number; `dev50` is.

### 13.3 `bcpsrvc2-smoke-val5` ✅

5 questions outside `dev150`, 30 steps each, not judged: a check that the parts work
together. No failed tool or role call. 2 trials submitted after a verifier round, 3 reached
the 30 steps. 429 documents read: 330 `none`, 31 `weak`, 68 `strong`; the reader used an
action on 83 (19%). 2,218 tokens per document (repeated prefix counted once).

### 13.4 `bcpsrvc2-dev50` ✅

Run on 2026-11-23, 06:28 to 12:19 UTC, with `VERSION=v02 bcp/run_bcpsrvc.sh dev50
bcpsrvc2-dev50`; no error in the tools log, no failed reader call, no trial exception. The
full tables are in [13](13_bcp_results.md) §10.

| `dev50` | Baseline | v0.1 | v0.2 |
| --- | ---: | ---: | ---: |
| Accuracy | 28.0% (14/50) | 54.0% (27/50) | **52.0%** (26/50), interval 38.5 to 65.2 |
| PFLOPs per question, every role prompt token counted | 3.20 | 11.32 | **9.21** |
| … a reader conversation's repeated prefix counted once | 3.20 | 11.32 | **6.79** |
| … and the prefix shared by the reader calls of a trial counted once (`13` §10.1) | 3.20 | 9.56 | **3.87** |
| Reader tokens per document (second rule) | | 4,598 | 2,178 |
| Gold documents passed on at their first reading | | 95.2% (100 of 105) | 91.3% (95 of 104) |

- Against v0.1: both right 22, v0.1 only 5, v0.2 only 4, both wrong 19 (McNemar p = 1.0).
  Against the baseline: 13 gained, 1 lost (p = 0.002).
- The reader took no action on 80% of the documents, read 5.6% of the lines of the
  documents it was given, and acted on 87.5% of the documents it graded strong but on 1.7%
  of those it graded `none`. The 9 gold documents it graded `none` were all graded from the
  first window without an action; 2 questions (q61, q1110) were lost that way.
- The first prompt of the v0.2 reader is 70% fixed text (skill, question, state
  table: about 1,280 of 1,820 tokens), so counting every prompt token gives a saving of 19%
  only. What the model server computes is closer to the third row, since vLLM reuses a
  shared prefix.
- The failures have the shape of v0.1's: 14 without a gold document from any search, 2 at
  the reader, 8 with a gold document on a card and no correct answer. 33 trials ran to the
  100-call limit (78% of the compute, 9 correct); 7 answer files left at the limit are
  wrong (v0.1: 3), none of the 17 submitted answers.

**For the owner to decide** 🔶: (1) which of the three rules counts the reader's compute in
the thesis; (2) whether v0.2 replaces v0.1 as the harness that goes to more questions;
(3) the next target: the trials that run to the limit (a text-only stop signal, the
verifier and the fallback answer), or the reader's `none` from the first window.

