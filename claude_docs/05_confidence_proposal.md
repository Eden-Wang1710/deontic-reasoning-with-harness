# 05 · Thesis Proposal: Enhancing Agentic Deontic Reasoning with Self-Verification and Confidence Calibration

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
([03](03_deonticbench_review.md)); DAR studies agentic harnesses that give the model on-demand
access to the rules and tool-assisted reasoning ([04](04_dar_review.md)).

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
and runs Prolog programs as part of solving (see [04](04_dar_review.md)). The symbolic verifier
reuses that infrastructure but targets intermediate claims, not just the final program.

## 3. Evaluation

Three axes: answer accuracy, confidence calibration, and cost in a practical setting.

### 3.1 Accuracy

Final-answer accuracy **before vs. after** self-verification and answer regeneration.

Repo note ✅: correctness follows DAR's grader, which is exact integer match for numeric answers and
case-insensitive string match otherwise; macro-F1 is used for the binary subdomains
([04](04_dar_review.md) §2.3). No ±$1 tolerance. Numbers are not comparable with the DeonticBench
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
([04](04_dar_review.md)). SARA `whole` (100 numeric, 276 binary) exists in DeonticBench but has not
been generated as Harbor tasks 🔶. With sets this small, every reported number needs a confidence
interval.

### 4.2 Models

| Model | Access | Evaluated with |
|---|---|---|
| Open-weight Qwen, e.g. **Qwen3.5-35B-A3B** | Self-hosted, hidden states accessible | Rubric-based self-verification **and** representation-based probing (§5.1) |
| A frontier **API** model | API only | Rubric-based self-verification only |

### 4.3 Evaluation protocol on SARA

- **Calibration**: ECE and Kuiper of the rubric-derived confidence against baselines, in
  particular **self-verbalised confidence** (the model states a number directly).
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
  missing from the released code ([04](04_dar_review.md) §3); ask the author.
- **Not yet fixed**: the exact rubric wording; the functional form of the rubric → confidence
  mapping; which frontier API model; the human-review cost and penalty parameters (take them from
  Jurayj et al.).

## 7. References and where their summaries live

Chinese GPT-written summaries in `paper_review_gpt/` are secondary; check the original before
relying on a number.

| Reference | Role in the proposal | Summary |
|---|---|---|
| Dou et al., 2026a — DeonticBench (arXiv 2604.04443) | Benchmark | `paper_review_gpt/01_deonticbench.md`, [03](03_deonticbench_review.md) |
| Dou et al., 2026b — DAR (arXiv 2606.05009) | Harness the module is added to | `paper_review_gpt/02_dar_deontic_reasoning_with_agentic_harnesses.md`, [04](04_dar_review.md) |
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
| 2026-10-15 | Pass-1 native trajectories use **Qwen3.5-35B-A3B served locally on 0238** (vLLM, bf16, thinking on, sampling left at Harbor's terminus-2 default T 0.7 with the server's top-p/top-k, 16k output tokens per turn; corrected 2026-10-16, an earlier version of this row wrongly said T 1.0), DAR's `terminus-2` × `deonticbench-direct` first, `--n-concurrent 4` | The model the proposal names and the one that degrades under the harness in DAR's Table 1; no OpenRouter key yet; bf16 keeps numbers paper-comparable. Details in `07` §1. |
| 2026-10-15 | Pass-1 scope restricted to **SARA Numeric + SARA Binary** (65 tasks per framing); Airline and USCIS-AAO deferred | SARA is the evaluation benchmark of §4.1 and the break-even analysis needs SARA-Numeric; saves ~2/3 of local compute per pass. |
| 2026-10-16 | Agent timeout is DAR's 600 s × 3 = 1,800 s locally, and **every run reports two accuracies**: at 1,800 s and recomputed at the paper's 600 s limit (trials over 600 s counted wrong) | Local throughput (~60 tok/s) is far below the APIs the paper used; 20/35 Qwen numeric trials exceed 600 s. The 600 s figure is the paper-comparable one, the 1,800 s figure measures reasoning quality. |
| 2026-10-16 | **terminus-2 output truncation**: keep Harbor's 10,000-byte observation limit but run with a truthful marker (`tools/dar_terminus2_local.py`, `AGENT=t2fix`); stock terminus-2 numbers are kept as the paper-comparable row | Stock terminus-2 applies `_limit_output_length` twice, so the model is told "71 interior bytes omitted" when 17,205 bytes of the statute are missing (`07` §1.5). The 10 KB limit itself is part of DAR's task design (the agent navigates the statute with shell tools), so it stays; only the misleading count is fixed. Options considered: (a) stock, (b) 30,000 bytes as in KIRA, (c) 10 KB + truthful marker; owner chose (c), with (a) retained for comparison. |
| 2026-10-16 | **Sampling temperature stays 0.7** (Harbor's terminus-2 default, inherited by KIRA) | DAR's run scripts set no temperature, so the paper's numbers were produced at 0.7 (🔶 inferred; the paper is silent). Qwen's model card recommends 1.0 for thinking mode, but with single-sample n = 35 the difference is below sampling noise; 1.0 is a possible ablation, not the baseline. |
| 2026-10-16 | **Agent time limit stays 600 s × 3 = 1,800 s**, with the 600 s re-scoring always reported alongside | DAR served open models "via vLLM or OpenRouter"; Qwen3.5-35B is in no released run script, so its backend is unknown. Wall-clock limits mix hardware into the result (98% of agent time here is waiting on the LLM at ~17 tok/s per trial); 1,800 s removes the hardware cap locally, the 600 s column keeps the paper's protocol. A token budget is the intended long-term definition. |
