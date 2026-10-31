# 04 · Initial Proposal: Enhancing Agentic Deontic Reasoning with Self-Verification and Confidence Calibration

- **Date**: 2026-10-14
- **Status**: this is the thesis direction. It transcribes the independent-study proposal
  (`Proposal_Independent_Study_Zixuan.pdf`, "Enhancing Agentic Deontic Reasoning with
  Self-Verification and Confidence Calibration", Zixuan Wang, JHU CS), which has been reviewed with
  the senior PhD student (Guangyao Dou) and the advisor. The PDF is the authoritative text; this file
  restates it so that every session has the plan in context, and adds repo-specific notes.
- **Replaces**: the earlier version of this file (a Claude-drafted "trace-based confidence" plan,
  2026-09-27). That draft is superseded in full and survives only in git history.
- **This file also holds the project decision log** (§8).
- **Confidence convention**: ✅ = checked directly against code, data or a web page on the date
  given. 🔶 = expectation, inference, or a number taken from a secondary summary.
- **Nothing here has been run.** No trajectories have been collected and no metric computed.

## 1. Background and motivation

Deontic reasoning is the derivation of obligations, permissions and prohibitions from explicit
rules and case-specific facts. DeonticBench benchmarks this across legal and policy domains
([03](03_background_review.md)); DAR studies agentic harnesses that give the model on-demand
access to the rules and tool-assisted reasoning ([03](03_background_review.md) Part II).

In high-stakes domains such as tax, a wrong prediction has a real price: overpayment is lost
money, substantial underpayment draws penalties (Jurayj et al., 2026). Estimating how likely an
answer is to be correct therefore matters in its own right, because it identifies the uncertain
cases that should go to a human.

The project has two goals:

1. **Improve accuracy** of agentic deontic reasoning through self-verification: rubric-based and
   symbolic checks that find reasoning errors and guide answer revision.
2. **Improve reliability** through calibrated confidence in the final answer, so the system can
   decide whether to accept the answer or abstain.

## 2. Proposed approach

A module is integrated into the DAR harness that (a) performs self-verification on a completed
trajectory and (b) estimates the probability that the agent's final answer is correct. Both
verification approaches below **reuse the same model that produced the trajectory**, through
additional inference calls. The input to either approach is a completed candidate trajectory:
case facts, retrieved rules, tool calls, reasoning trace and the candidate answer. Either approach
can feed back to the agent to drive further reasoning and answer revision. Only the rubric-based
approach additionally yields a confidence estimate for the final retained answer.

### 2.1 Rubric-based self-verification

The model reviews its own trajectory and reasoning against a **manually initialised rubric set**
tailored to rule-grounded reasoning. Inspired by process evaluations (Guan et al., 2025,
*Monitoring Monitorability*), the initial rubrics check:

1. **Tool use**: whether tool calls use valid formats and appropriate arguments.
2. **Provision coverage**: whether the agent checked the relevant provisions, including their
   conditions, exceptions and cross-references.
3. **Grounded reasoning**: whether the reasoning follows from those provisions and the case facts.
   Concretely: which provisions were used, and whether their conditions and exceptions were applied
   correctly to the facts.

For each criterion the model reports one of **satisfied / violated / cannot be assessed** from the
available evidence. The agent can then use this feedback to revise its answer.

Two questions are explored on top of this:

- **Rubric → confidence.** How rubric assessments map to a calibrated confidence `p ∈ [0, 1]` that
  the final retained answer is correct. The mapping and its calibration are developed on held-out
  data using the metrics in §3, and the resulting `p` drives the accept / abstain decision.
- **Rubric self-improvement.** Following trajectory-based failure analysis and rubric evolution
  (Wan et al., 2026; Zhu et al., 2026, *SkillCoach*), failed trajectories and verification errors
  are analysed to find recurring failure modes and to propose additions or revisions to the
  criteria.

### 2.2 Symbolic reasoning verification

Inspired by SymDiag (Cui et al., 2026), Prolog is used to check the agent's reasoning. The model
encodes the relevant rules and case facts in Prolog and generates queries that test its
**intermediate conclusions**. When a check fails, the harness tells the agent which step is wrong
and why, and the agent regenerates an answer.

Repo note ✅ (2026-10-14): the DAR container already has `swipl`, and the DAR agent already writes
and runs Prolog programs as part of solving (see [03](03_background_review.md) Part II). The symbolic verifier
reuses that infrastructure but targets intermediate claims, not just the final program.

## 3. Evaluation

Three axes: answer accuracy, confidence calibration, and cost in a practical setting.

### 3.1 Accuracy

Final-answer accuracy **before vs. after** self-verification and answer regeneration.

Repo note ✅: correctness follows DAR's grader, which is exact integer match for numeric answers and
case-insensitive string match otherwise; macro-F1 is used for the binary subdomains
([03](03_background_review.md) §6.3). No ±$1 tolerance. Numbers are not comparable with the DeonticBench
paper without re-scoring (see `CLAUDE.md`).

### 3.2 Confidence calibration

Agreement between predicted confidence and empirical accuracy, measured by ECE and a
confidence-weighted Kuiper-based measure (Radharapu et al., 2025). Both are lower-is-better and
neither measures accuracy.

**ECE** with `M` confidence bins over `N` examples, where `B_m` is the set of examples in bin `m`,
`p̄_m` its mean predicted confidence and `ȳ_m` its empirical accuracy:

```
ECE = Σ_{m=1..M} (|B_m| / N) · |p̄_m − ȳ_m|
```

**Kuiper-based measure**, binning-free. Sort predicted confidences `p_1 ≤ … ≤ p_N` with correctness
labels `y_i ∈ {0, 1}`, set `C_0 = 0`, and compute

```
C_k    = (1/N) · Σ_{i=1..k} p_i · (y_i − p_i),   k = 1..N
Kuiper = max_{0≤k≤N} C_k − min_{0≤k≤N} C_k
```

`C_k` is the cumulative weighted discrepancy over the first `k` examples; the weight `p_i`
emphasises high-confidence predictions. (Formulas transcribed from the proposal; re-check against
Radharapu et al. before they appear in the thesis.)

### 3.3 Cost and efficiency

For numerical tax tasks, follow Jurayj et al. (2026) and report the **average cost per case**,
which counts losses from overpayment, penalties for substantial underpayment, and an assumed cost of
human review. The question is whether **referring low-confidence cases to a human** lowers this
cost compared with (a) accepting every agent answer and (b) sending every case to a human.

The additional **inference cost and latency** introduced by verification and confidence estimation
are reported separately.

## 4. Experiments

### 4.1 Data

Two roles for data:

| Role | Datasets | Used for |
|---|---|---|
| **Development / calibration** (auxiliary) | RuleArena **NBA** subset (Zhou et al., 2025); ShARC (Saeidi et al., 2018); ConditionalQA (Sun et al., 2022); FOLIO (Han et al., 2024) | Collect labelled DAR trajectories; refine the rubric set; fit and calibrate the rubric → confidence mapping; train representation probes (§5.1) |
| **Evaluation benchmark** | **SARA** tasks within DeonticBench, run through the DAR harness | Test whether confidence methods developed on the auxiliary data transfer; calibration vs. baselines; abstention cost on SARA-Numeric |

Trajectories on the auxiliary datasets are collected with the DAR harness, which means each
auxiliary dataset needs a Harbor adapter in the style of `harbor-deonticbench/adapters/` 🔶 (none
exists yet; see §6).

Repo note ✅: the vendored Harbor datasets hold the 173 `hard` tasks of four subdomains
(sara_numeric 35, sara_binary 30, airline 80, uscis-aao 28); SARA `hard` is therefore 65 tasks
([03](03_background_review.md) Part II). SARA `whole` (100 numeric, 276 binary) exists in DeonticBench but has not
been generated as Harbor tasks 🔶. With sets this small, every reported number needs a confidence
interval.

### 4.2 Models

| Model | Access | Evaluated with |
|---|---|---|
| Open-weight Qwen, e.g. **Qwen3.5-35B-A3B** | Self-hosted, hidden states accessible | Rubric-based self-verification **and** representation-based probing (§5.1) |
| A frontier **API** model | API only | Rubric-based self-verification only |

### 4.3 Evaluation protocol on SARA

- **Calibration**: ECE and Kuiper of the rubric-derived confidence against baselines, in
  particular **self-verbalised confidence** (the model states a number directly). The baseline
  is fixed as the in-context elicitation of [07](07_verbalized_confidence.md) §2.1 (decision
  2026-10-21, §8); measured on Qwen3.5-35B-A3B it is at chance on SARA-Numeric (AUROC 0.35 /
  0.54, ECE 0.69 / 0.59) and uninformative on SARA-Binary.
- **Abstention cost** on SARA-Numeric: the break-even price metric of Jurayj et al. (2026), testing
  whether better confidence estimates reduce the combined cost of prediction errors and human
  review.
- **Overhead**: additional inference cost and latency, reported separately from accuracy.

## 5. Extended exploration

### 5.1 Representation-based confidence probing

For models whose hidden states are accessible (the Qwen model), train lightweight confidence
predictors on representations extracted **during verification**. Following Radharapu et al.
(2025), these are linear probes predicting final-answer correctness, with labels derived from task
ground truth. Compare their calibration and computational overhead with the rubric-based estimate.

### 5.2 Harness distillation

Inspired by Harness-Zero (Ye et al., 2026), distil the behaviours induced by the
self-verification-enhanced harness into the open-weight model. Rubric-guided verification and
correction trajectories (identifying reasoning gaps, inspecting missing rules, revising answers)
serve as fine-tuning supervision for Qwen. The test is whether the fine-tuned model **retains these
behaviours under standard harnesses** (`terminus-2`, Terminus-KIRA) without the added verification
module, evaluated by final-answer accuracy and break-even price.

## 6. Resources and open items

- **No trajectories exist yet** ✅ (re-checked 2026-10-14: `outputs/`, `bootstrap_results/` and
  `jobs/` are gitignored in both subtrees; the HuggingFace dataset carries cases, labels and
  reference Prolog only). Pass-1 trajectories must be produced by running DAR, or obtained from the
  author.
- **Compute**: running DAR needs Docker and `swipl` on a server (see
  [02](02_setup_and_sync.md) for per-machine status). Hosting Qwen3.5-35B-A3B with hidden-state
  access, probe training and distillation need GPUs. This is the resource request the proposal
  supports.
- **Adapters for auxiliary datasets** (RuleArena-NBA, ShARC, ConditionalQA, FOLIO): none exist in
  the vendored code ✅; they have to be written, outside the subtree directories.
- **Where our code lives**: outside `DeonticBench/` and `harbor-deonticbench/` (subtree rule in
  `CLAUDE.md`). The verification module should hook into the harness as a post-trajectory step
  rather than editing Harbor internals, to keep `git subtree pull` clean 🔶 (exact integration
  point to be decided once the harness has been run).
- **Still open from the DAR review**: `kira_deontic_grounded` and `kira_compute_then_answer` are
  missing from the released code ([03](03_background_review.md) §7); ask the author.
- **Not yet fixed**: the exact rubric wording; the functional form of the rubric → confidence
  mapping; which frontier API model; the human-review cost and penalty parameters (take them from
  Jurayj et al.).

## 7. References and where their summaries live

Chinese GPT-written summaries in `paper_review_gpt/` are secondary; check the original before
relying on a number.

| Reference | Role in the proposal | Summary |
|---|---|---|
| Dou et al., 2026a — DeonticBench (arXiv 2604.04443) | Benchmark | `paper_review_gpt/01_deonticbench.md`, [03](03_background_review.md) |
| Dou et al., 2026b — DAR (arXiv 2606.05009) | Harness the module is added to | `paper_review_gpt/02_dar_deontic_reasoning_with_agentic_harnesses.md`, [03](03_background_review.md) Part II |
| Jurayj, Holzenberger, Van Durme, AAAI 2026 — LMs and logic programs for trustworthy tax reasoning | Cost model, break-even price, human-review abstention | `paper_review_gpt/03_trustworthy_tax_reasoning.md` |
| Radharapu et al., 2025 — Calibrating LLM judges (arXiv 2512.22245) | ECE, Kuiper measure, linear probes | `paper_review_gpt/04_calibrating_llm_judges.md` |
| Ye et al., 2026 — Harness-Zero (arXiv 2609.24974) | Harness distillation | `paper_review_gpt/05_harness_zero.md` |
| Cui et al., 2026 — SymDiag (KDD 2026, arXiv 2608.08786) | Prolog-based symbolic verification | `paper_review_gpt/06_symdiag.md` |
| Guan et al., 2025 — Monitoring monitorability (arXiv 2512.18311) | Process-evaluation rubrics | `paper_review_gpt/08_monitoring_monitorability.md` |
| Wan et al., Findings of ACL 2026 — Test-time rubric-guided verification | Rubric evolution | — |
| Zhu et al., 2026 — SkillCoach (arXiv 2607.01874) | Self-evolving rubrics | — |
| Zhou et al., ACL 2025 — RuleArena | Auxiliary data (NBA subset) | — |
| Saeidi et al., EMNLP 2018 — ShARC | Auxiliary data | — |
| Sun, Cohen, Salakhutdinov, ACL 2022 — ConditionalQA | Auxiliary data | — |
| Han et al., EMNLP 2024 — FOLIO | Auxiliary data | — |

## 8. Decision log

| Date | Decision | Rationale |
|---|---|---|
| 2026-09-17 | Both upstream repos vendored as git subtrees; `harbor-deonticbench/datasets/` tracked in git rather than regenerated per machine | Pinned tasks mean every machine evaluates byte-identical inputs; regeneration drifts silently when `DeonticBench/` moves. See `02`. |
| 2026-10-14 | Thesis direction fixed to the reviewed proposal: self-verification (rubric-based + Prolog symbolic) and confidence calibration inside the DAR harness, evaluated by accuracy, ECE/Kuiper and break-even price | Reviewed with the senior PhD student and the advisor. Supersedes the Claude-drafted 2026-09-27 plan and all of its decisions. |
| 2026-10-14 | Verifier reuses the solver model via extra inference calls | As stated in the proposal; keeps the module model-agnostic and applicable to API models. |
| 2026-10-14 | Development on auxiliary datasets (RuleArena-NBA, ShARC, ConditionalQA, FOLIO), evaluation on SARA via DAR | SARA `hard` is too small to both fit and evaluate a calibration mapping; transfer to SARA is itself a research question. |
| 2026-10-14 | Correctness on SARA graded by DAR's rule (exact integer match, macro-F1 for binary) | The module lives in the DAR harness, so it is scored against that harness's own grader. |
| 2026-10-15 | Pass-1 native trajectories use **Qwen3.5-35B-A3B served locally on 0238** (vLLM, bf16, thinking on, sampling left at Harbor's terminus-2 default T 0.7 with the server's top-p/top-k, 16k output tokens per turn; corrected 2026-10-16, an earlier version of this row wrongly said T 1.0), DAR's `terminus-2` × `deonticbench-direct` first, `--n-concurrent 4` | The model the proposal names and the one that degrades under the harness in DAR's Table 1; no OpenRouter key yet; bf16 keeps numbers paper-comparable. Details in `06` §1. |
| 2026-10-15 | Pass-1 scope restricted to **SARA Numeric + SARA Binary** (65 tasks per framing); Airline and USCIS-AAO deferred | SARA is the evaluation benchmark of §4.1 and the break-even analysis needs SARA-Numeric; saves ~2/3 of local compute per pass. |
| 2026-10-16 | Agent timeout is DAR's 600 s × 3 = 1,800 s locally, and **every run reports two accuracies**: at 1,800 s and recomputed at the paper's 600 s limit (trials over 600 s counted wrong) | Local throughput (~60 tok/s) is far below the APIs the paper used; 20/35 Qwen numeric trials exceed 600 s. The 600 s figure is the paper-comparable one, the 1,800 s figure measures reasoning quality. |
| 2026-10-16 | **terminus-2 output truncation**: keep Harbor's 10,000-byte observation limit but run with a truthful marker (`tools/dar_terminus2_local.py`, `AGENT=t2fix`); stock terminus-2 numbers are kept as the paper-comparable row | Stock terminus-2 applies `_limit_output_length` twice, so the model is told "71 interior bytes omitted" when 17,205 bytes of the statute are missing (`06` §1.5). The 10 KB limit itself is part of DAR's task design (the agent navigates the statute with shell tools), so it stays; only the misleading count is fixed. Options considered: (a) stock, (b) 30,000 bytes as in KIRA, (c) 10 KB + truthful marker; owner chose (c), with (a) retained for comparison. |
| 2026-10-16 | **Sampling temperature stays 0.7** (Harbor's terminus-2 default, inherited by KIRA) | DAR's run scripts set no temperature, so the paper's numbers were produced at 0.7 (🔶 inferred; the paper is silent). Qwen's model card recommends 1.0 for thinking mode, but with single-sample n = 35 the difference is below sampling noise; 1.0 is a possible ablation, not the baseline. |
| 2026-10-16 | **Agent time limit stays 600 s × 3 = 1,800 s**, with the 600 s re-scoring always reported alongside | DAR served open models "via vLLM or OpenRouter"; Qwen3.5-35B is in no released run script, so its backend is unknown. Wall-clock limits mix hardware into the result (98% of agent time here is waiting on the LLM at ~17 tok/s per trial); 1,800 s removes the hardware cap locally, the 600 s column keeps the paper's protocol. A token budget is the intended long-term definition. |
| 2026-10-21 | **Confidence-calibration baseline = in-context self-verbalised confidence** (`07` §2.1): one extra turn appended to the agent's own conversation at task completion, asking for the probability that the final answer is exactly correct. The answer-only elicitation is *not* a baseline; it is reclassified as the first verification method (an independent re-solve, `07` §4). | Owner's review of `07`: a baseline must measure the agent's self-assessment in the state the harness leaves it; answer-only hands a fresh judge the full statute and re-solves the case, which is a verifier design (legitimate, since the statute is in the container, but a different thing). Measured baseline on Qwen3.5-35B-A3B: numeric AUROC 0.35 / 0.54 with CIs covering 0.5, mean confidence 0.86–0.90 at accuracy 0.17–0.31; binary ECE 0.09–0.13 but no separation of the 4 errors. Reported metrics: accuracy, mean confidence, AUROC, ECE, Kuiper; Kuiper/Brier lead because ECE's 10 bins are arbitrary (sensitivity ≤ 0.03 here). |
| 2026-10-25 | **Self-verbalised confidence is closed as a baseline** (`07` §2.1–2.4): post-hoc and answer-step (`lastturn`) elicitations give the same near-constant confidence (0.86–0.97) on both harnesses and in the direct setting, with AUROC indistinguishable from 0.5; the elicitation point is not pursued further. **Working target for the module: AUROC ≥ 0.7 and ECE ≤ 0.15 on SARA numeric, both at once.** Next work: rubric-based self-verification and confidence calibration (new session). Open follow-up only: 3–5 draws per answer-step history to separate sampling noise from the format effect behind the binary answer flips (`07` §2.3). | Reviewed with the owner 2026-10-25 on the side-by-side table of `07` §2.4. ECE/Kuiper collapse to \|mean conf − accuracy\| when the confidence is constant, so no change of elicitation timing can improve them; the signal has to come from evidence other than the answer (rubrics, independent re-solve). |
| 2026-10-26 | **First self-verification module = a rubric verifier sub-agent on terminus-2** (`08`): at every completion claim the harness runs a verifier in a fresh `Chat` (same model) with `tools/skills/rubric_verifier/SKILL.md`, the trajectory as the solver saw it (no thinking) and shell access via `environment.exec` (≤ 8 command steps); its report replaces the "Are you sure?" message; ≤ 3 rounds, then the solver must finish with a top-level `confidence`. Base agent t2fix; 1,800 s wall clock unchanged; no new control conditions (compare with `sara-t2fix-run1`); skill hand-written v1, optimisation deferred. **The harness holds no checking logic**: rubrics, evidence rules and the no-alternative-answer rule live in the skill only. | Owner's design review 2026-10-25/26. De-anchoring is the motivation (`07` §2.2 vs §2.1); a separate context also bounds the solver's context growth under up to three repair rounds. Host-side filters would be benchmark-specific hard-coding and were rejected; stock completion semantics are kept so the only change is the confirmation message. |
| 2026-10-26 | **Run-2 of the rubric verifier** (`08` §8): the solver writes a three-section answer file (statute quotes with line numbers, reasoning, `Answer:` last; grader unchanged) via a swapped terminus-2 prompt template; the verifier gets an index of the trajectory and reads the full text from a file in the container; skill v2; no confidence field; vLLM prefix caching switched on for all subsequent runs (`PREFIX_CACHE=0` reproduces the old server). No new control conditions; baseline not re-run. Timed-out trials: protocol number counts them wrong, the file-graded number is reported alongside. | Run-1 (`08` §7.2): repairs happened only on the error class visible in the solver's text (6/24 numeric, all standard-deduction); FUTA / dependents / status were never questioned; 11 timeouts from un-cached prefill contention; three right→wrong cases came from reports that supplied a wrong replacement; stated confidence constant at 100 %. The record and the verifier are both parts of one harness method, so they are not ablated separately (owner). |
| 2026-10-26 | **Run-3 of the rubric verifier** (`08` §9): the answer file's `## Statute` section lists section numbers only and `## Reasoning` is free text (template v2); skill v3 with a fixed four-step plan (locate, read, check, report; `verifier_max_steps=4`), one command step on later rounds, `NO` reserved for violations checked against the file and `UNKNOWN` for leads, an `UNUSED` fact reported as `NO` only with a section that changes what the question asks about, "the facts do not state X" never a `NO` on entailment tasks; the solver's decision text says a `NO` is a checked violation and an `UNKNOWN` a lead; `--n-concurrent 2`; verifier thinking stays on; rounds 3, wall clock 1,800 s. | Owner's decisions after `08` §8.2: the verbatim-quote + line-number requirement cost the solver ≈370 s per numeric trial in search episodes and the verifier re-verified quotes line by line without catching either damage; verifier time is its own generation (59 ms/token) so savings must come from fewer calls, not caching; per-request generation is 24 tok/s at 2 concurrent vs 16 at 4 and the wall clock is per trial. Disabling the verifier's thinking was rejected as a change of method rather than of efficiency. |
| 2026-10-28 | **The method is named the solver-verifier harness** (`08`): terminus-2 solver + rubric verifier sub-agent, run-3 configuration (section-number record, skill v3, four-step verifier). Docs reorganised: one background review `03` (DeonticBench, DAR, data statistics as Parts I–III), this file is the initial proposal `04`, repo map `05`, experiments `06`-`08`. | Owner's naming and ordering choice for the thesis write-up; all cross-references rewritten in the same commit. |
| 2026-10-28 | **Reader-verifier harness, version 1** (`09`): the run-3 solver calls two sub-agents as tools through a top-level `subagent` field in its JSON response (`reader` with a query; `verifier` without arguments); the reader runs in a fresh `Chat` with `tools/skills/reader_v1/SKILL.md`, sees the task text and the query, returns **line ranges** that the harness extracts verbatim (`sed -n`, ≤ 16,000 characters, 3 command steps); the verifier is run-3's mechanics with skill v4. Completion: the prompt says to call the verifier before finishing, and the harness accepts `task_complete` only when the answer file's hash, taken after the episode's commands, equals the hash the verifier last saw; a verified claim ends the trial at once (no "Are you sure?"); an unverified one is bounced, at most twice, then accepted and recorded; a call plus `task_complete` in one response is a call. The solver may still read the rule file itself (counted, not blocked). Reader calls unlimited; 3 verifier rounds; no confidence field; one draw on the 65 SARA tasks; **no control conditions** (no compactor-less / full-statute / second-draw ablations). Version 2 (a compactor that rebuilds the solver's context before a repair) deferred. | Owner's review of GPT's two-stage plan, 2026-10-28. The solver's conversation, not retrieval, is the bottleneck: SARA's statute is 6.1k tokens, so direct reasoning already sees all of it and any retrieval can only lose information; the harness has to win on a clean context, tool arithmetic and verification. Line ranges rather than model-copied text because a 6k-token copy would cost ≈6 min of generation at 59 ms/token; a hash gate rather than prompt-only because run-3's Qwen ignored skill-level budget rules; the gate holds no checking logic. The JSON field keeps terminus-2's text-based loop and parser unchanged. |
| 2026-10-28 | **One skill per role; verifier rubrics reorganised to four** (`09` §5–6): the solver's prompt is the stock terminus-2 template plus the `subagent` field and a `{skill}` slot filled from `tools/skills/solver_v1/SKILL.md`; skill v4 has four rubrics in the proposal's order, tool use → provision coverage → provision selection and application → grounded reasoning (v3's format check dropped, not moved to the harness; v3's rubric 5 split into "done with a tool" under 1 and "reproduces" under 4; selection before grounding). Coverage is defined once, in the reader's and the verifier's skills alike: a provision is covered when its text with its conditions and exceptions has reached the solver; the reader's job is to make it true, the verifier's to check it. The verifier keeps reading the full trajectory from the container file. All three skills shortened. | Owner's review 2026-10-28: the solver prompt had grown too far from the baseline's; a multi-agent design should decouple responsibilities, so each check sits with the agent that has the inputs for it (the reader has neither the answer file nor the trajectory and is stateless, so it cannot check coverage); a hard-coded format gate in the harness was rejected as benchmark-specific. |
| 2026-10-31 | **GPU use on BRTX always goes through Slurm** (`srun`/`sbatch`), including long-lived vLLM servers; we never start GPU processes directly on a node, and every job verifies at start that its allocated GPUs are actually free (`02` §3.1) | The lab wiki rule. On 2026-10-30 another user's two vLLM servers were found running outside Slurm on brtx602 for 32 and 11 days, invisible to `squeue`, so Slurm could hand their GPUs to a new job and collide. Bypassing Slurm ourselves would make the problem worse; the 24 h `ba100` limit is handled by a restartable `sbatch` script instead. |
