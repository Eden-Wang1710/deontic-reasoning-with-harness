# 08 · Self-Verbalised Confidence on Native Trajectories

- **Date opened**: 2026-10-16
- **Purpose**: the first confidence baseline of the proposal ([05](05_confidence_proposal.md)
  §4.3): after the agent has marked a task complete, ask the *same* model for the probability
  that its final answer is exactly correct, and score that number with the calibration metrics
  of [05](05_confidence_proposal.md) §3.2. Everything later (rubric-derived confidence, Prolog
  verification) is compared against this.
- **Baseline definition** (owner's decision 2026-10-21, logged in [05](05_confidence_proposal.md)
  §8): the **in-context** condition (§2.1) *is* the self-verbalised confidence baseline. The
  **answer-only** condition is not a baseline but a first verification method (an independent
  re-solve, §4); it is kept in §2.2 for reference.
- **Code**: `tools/verbalized_confidence.py`. Raw per-trial outputs under
  `outputs/verbalized_confidence/<run>/` on 0238 (gitignored); only the numbers here are shared.
- **Confidence convention**: ✅ checked in the run output on the date given, 🔶 inference.

## 1. Method ✅ 2026-10-16

| Item | Value |
|---|---|
| Trajectories | Pass-1 runs from [07](07_native_trajectories.md): terminus-2 × direct (`sara-t2-run1`, 65 trials) and KIRA × direct (`sara-kira-run1`, finished trials). SARA numeric + binary only. |
| Model | The same served Qwen3.5-35B-A3B (bf16, thinking on, model-default sampling T 1.0 / top-p 0.95 / top-k 20), one extra call per trial, `max_tokens` 8,192. Sampling is stochastic, so a single call is one draw 🔶 (repeat draws are a cheap follow-up). |
| Condition `incontext` | The agent's own conversation at the moment it confirmed `task_complete`, then one user turn asking for confidence. terminus-2: the exact `messages` of the last LLM call (from `agent/episode-N/debug.json`) plus its final assistant content. KIRA: rebuilt from `agent/trajectory.json` in KIRA's native format (assistant `tool_calls` → `tool: executed` → user terminal output); the elicitation is appended to the last terminal output. Thinking of earlier turns is *not* in the history, as in the real runs. |
| Condition `answeronly` | A fresh context: the statute, the task instruction and the agent's final answer, no trajectory. A judge-style baseline showing how much the trajectory context changes the estimate. |
| Condition `lastturn` (added 2026-10-23, owner's proposal) | The conversation as it was *before* the step that wrote the answer file (terminus-2: that episode's `debug.json` messages; KIRA: trajectory steps before the answer-writing `execute_commands`), with the last terminal output extended by a request to reply in plain text with `Answer: <value>` and `Confidence: NN%` in the same response. The answer is therefore **re-sampled** (T 0.7, 16,384 tokens, thinking on) and re-graded with DAR's `test.sh` rule; records keep `reward` (new answer) and `reward_orig` (trajectory). Differences from `incontext`: the confidence is produced with the live thinking of the answer step, before any completion-confirmation turn, and costs nothing extra in a real harness. |
| Direct analogues (`tools/db_direct_confidence.py`, 2026-10-23) | `joint`: the stored direct prompt of run 3 plus "end with `Confidence: NN%`", one new generation per case, graded with DAR's exact rule (`reward`) and DeonticBench's ±1 (`reward_pm1`). `posthoc`: each stored generation replayed as the assistant turn plus the in-context elicitation (258 ok generations). |
| Elicitation | "…estimate how likely it is that this answer is exactly correct … The grader uses exact match … Consider whether you may have misread or misapplied a provision, missed a cross-reference or an exception, misjudged filing status or a dependency, or made an arithmetic slip. Reply in plain text, not JSON … first line exactly `Confidence: <0–100>%` followed by at most three sentences naming the main risk." (full text in the script) |
| Label | DAR's verifier reward (exact match), from `verifier/test-stdout.txt`. |
| Metrics | ECE (10 equal-width bins), the confidence-weighted Kuiper measure as transcribed in [05](05_confidence_proposal.md) §3.2, Brier, AUROC (correct vs. wrong), and selective accuracy / coverage at confidence ≥ 0.5 / 0.7 / 0.9. Reliability table with 5 bins. |

The two elicitations are not word-for-word parallel: the in-context prompt lists four risk
classes to consider ("misread or misapplied a provision, missed a cross-reference or an
exception, misjudged filing status or a dependency, or made an arithmetic slip"), the
answer-only prompt does not and instead says "You may reason about the statute and the facts as
much as you need". Noted 2026-10-21 as a confound to remove before either prompt is reused.

Parsing: `Confidence: NN%` on the first line; fallbacks: any `NN%`, then a bare probability;
unparsed answers are reported and excluded from the metrics.

## 2. Results

### 2.1 Baseline: in-context, both harnesses ✅ 2026-10-16/17 (`outputs/verbalized_confidence/{t2,kira}_incontext`)

terminus-2 records are the **stock** run `sara-t2-run1` (65 trials; no confidence pass on
`t2fix` yet). KIRA records are the **wrapper-repaired** pass: `sara-kira-run1` (56 finished)
plus `sara-kira-rerun9` (9), i.e. the merged 65-trial run of
[07](07_native_trajectories.md) §1.6. All 130 records parsed (one terminus-2 trial answered in
the agent's JSON format without a number on the first draw and was re-asked; a bare "15%" tax
rate in that reply is why the parser only accepts percentages tied to a confidence word).

| Harness | Subset | n | Accuracy | Mean confidence | AUROC ↑ | ECE ↓ | Kuiper ↓ |
|---|---|---|---|---|---|---|---|
| terminus-2 | SARA-Numeric | 35 | 0.171 (6/35) | 0.863 | 0.351 | 0.691 | 0.610 |
| terminus-2 | SARA-Binary | 30 | 0.867 (26/30) | 0.935 | 0.688 | 0.085 | 0.075 |
| KIRA | SARA-Numeric | 35 | 0.314 (11/35) | 0.900 | 0.538 | 0.586 | 0.528 |
| KIRA | SARA-Binary | 30 | 0.867 (26/30) | 0.945 | 0.375 | 0.128 | 0.113 |

One trial is 2.9 points of accuracy on numeric, 3.3 on binary. Brier and selective accuracy for
the same records are in the §2.2 table.

**Is the numeric AUROC below chance?** ✅ 2026-10-21 (20,000 label permutations, 5,000
bootstrap resamples, `random.seed(0)`):

| Harness | AUROC | one-sided permutation p (AUROC ≤ observed under no association) | bootstrap 95 % CI |
|---|---|---|---|
| terminus-2 | 0.351 | 0.13 | [0.10, 0.64] |
| KIRA | 0.538 | 0.65 | [0.34, 0.72] |

With 6 (resp. 11) positives the CIs include 0.5: the defensible statement is *indistinguishable
from chance*, with the terminus-2 point estimate on the inverted side. An earlier version of
§3.1 called it "anti-correlated"; that was over-reading the point estimate.

**ECE bin sensitivity** ✅ 2026-10-21. Stated values are coarse (in-context: 6 distinct values,
70/75/80/85/90/95; binary 95 % in 22 of 30), so the choice of 10 equal-width bins matters
little:

| Harness | Subset | \|mean conf − acc\| | ECE 5 bins | ECE 10 bins | ECE 15 bins | ECE 5 equal-mass bins |
|---|---|---|---|---|---|---|
| terminus-2 | numeric | 0.691 | 0.691 | 0.691 | 0.691 | 0.691 |
| terminus-2 | binary | 0.068 | 0.085 | 0.085 | 0.085 | 0.102 |
| KIRA | numeric | 0.586 | 0.586 | 0.586 | 0.586 | 0.586 |
| KIRA | binary | 0.078 | 0.118 | 0.128 | 0.142 | 0.145 |

On numeric every value sits above 0.6 and on the same side of the accuracy, so ECE equals the
plain gap whatever the binning. Differences elsewhere are ≤ 0.03, under one trial. Kuiper and
Brier need no bins and are the ones to lead with in the thesis; ECE is reported for readability.

Reliability (5 bins, terminus-2): binary — [0.8, 1.0] n = 29, conf 0.94, acc 0.86; numeric —
[0.6, 0.8) n = 7, conf 0.74, acc 0.43; [0.8, 1.0] n = 28, conf 0.89, acc 0.11. Mean extra cost
per trial: 1,073 (terminus-2) / 947 (KIRA) completion tokens on top of 7.1k / 10.3k prompt
tokens (the agent's own conversation, incl. the statute as the agent saw it), ~1 min each.

### 2.2 All four conditions, for reference ✅ 2026-10-17 (`outputs/verbalized_confidence/{t2,kira}_{incontext,answeronly}`)

KIRA records cover `sara-kira-run1` (56 finished trials) plus `sara-kira-rerun9`, i.e. the
merged 65-trial KIRA pass of [07](07_native_trajectories.md) §1.6. All 65 × 4 records parsed.

**Budget.** Answer-only elicitation with thinking on re-derives the whole case from the
statute before stating a number: at the original 8,192-token budget 25/35 terminus-2 and 15/27
KIRA numeric answers came back empty (`finish_reason = length`, no visible content). Those
records were redone at 16,384 tokens (field `max_tokens` in the record, `attempts = 2`); two
terminus-2 numeric replies from the first 8,192-token pass hit the limit after already emitting
a `Confidence: 100%` line and were kept as parsed.
Mean completion tokens per elicitation: in-context 1,073 (t2) / 947 (KIRA); answer-only 8,150
(t2) / 7,406 (KIRA), i.e. answer-only costs about as much as solving the task again.

| Harness | Condition | Subset | n | Acc | Mean conf | ECE | Kuiper | Brier | AUROC | cov / acc @0.5 | @0.7 | @0.9 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| terminus-2 | in-context | binary | 30 | 0.867 | 0.935 | 0.085 | 0.075 | 0.116 | 0.688 | 1.00 / 0.87 | 1.00 / 0.87 | 0.90 / 0.89 |
| terminus-2 | in-context | numeric | 35 | 0.171 | 0.863 | 0.691 | 0.610 | 0.639 | **0.351** | 1.00 / 0.17 | 1.00 / 0.17 | 0.51 / 0.11 |
| terminus-2 | answer-only | binary | 30 | 0.867 | 0.951 | 0.141 | 0.121 | 0.156 | 0.404 | 0.97 / 0.86 | 0.97 / 0.86 | 0.97 / 0.86 |
| terminus-2 | answer-only | numeric | 35 | 0.171 | 0.464 | 0.338 | 0.284 | 0.310 | **0.891** | 0.46 / 0.31 | 0.46 / 0.31 | 0.43 / 0.33 |
| KIRA | in-context | binary | 30 | 0.867 | 0.945 | 0.128 | 0.113 | 0.135 | 0.375 | 1.00 / 0.87 | 0.97 / 0.86 | 0.90 / 0.85 |
| KIRA | in-context | numeric | 35 | 0.314 | 0.900 | 0.586 | 0.528 | 0.555 | 0.538 | 1.00 / 0.31 | 0.97 / 0.32 | 0.71 / 0.32 |
| KIRA | answer-only | binary | 30 | 0.867 | 0.860 | 0.167 | 0.094 | 0.176 | 0.538 | 0.87 / 0.89 | 0.87 / 0.89 | 0.87 / 0.89 |
| KIRA | answer-only | numeric | 35 | 0.314 | 0.677 | 0.385 | 0.343 | 0.371 | **0.824** | 0.69 / 0.42 | 0.66 / 0.44 | 0.63 / 0.46 |

Metrics as defined in §1; `cov / acc @τ` = fraction of trials with confidence ≥ τ and the
accuracy on that subset. One trial is 2.9 points on numeric, 3.3 on binary; all AUROCs are on
≤ 11 positives (numeric) or ≤ 4 negatives (binary), so treat differences under ~0.15 as noise.

## 3. Observations ✅ 2026-10-16/17

### 3.1 terminus-2 in-context (n = 65)

- **Numeric confidence cannot separate right from wrong answers, and overstates by 60–70
  points.** AUROC 0.35 (terminus-2) / 0.54 (KIRA), both with CIs covering 0.5 (§2.1). Every
  numeric trial states ≥ 0.70, so a 0.5 threshold rejects nothing; thresholding at 0.9 keeps
  51 % of the terminus-2 numeric trials at 11 % accuracy, below the unfiltered 17 %. The
  terminus-2 point estimate is on the inverted side because three of the six correct answers
  stated 0.75 (the model named a risk, e.g. the exemption phase-out, and then handled it
  correctly) while 24 wrong answers stated ≥ 0.85 (memorised deduction amounts, stated
  confidently); with n = 6 this is an observation, not a result. As an abstention signal,
  in-context verbalised confidence on SARA-Numeric is useless for this model; the break-even
  analysis of [05](05_confidence_proposal.md) §3.3 will show it costs more than accepting
  everything 🔶. (Wording corrected 2026-10-21; see §2.1.)
- **Binary looks calibrated but is equally uninformative.** Mean confidence 0.94 vs. accuracy
  0.87 (ECE 0.085); the small gap is a coincidence between the model's habitual 0.95 and a task
  it gets right 87 % of the time, not self-knowledge: all four misses sit at 0.85–0.95 among 26
  hits at the same values. AUROC 0.69 (terminus-2) and 0.38 (KIRA) are noise over 4 negatives.
- **The stated risks are the right kind but the wrong instance.** The justification sentences
  name exactly the error classes seen in [07](07_native_trajectories.md) §1.5 (exemption
  phase-out, dependency status, standard-deduction amount), and in wrong trials the named risk
  is sometimes the actual error (e.g. `WFrmvX5`, `Sh3HGrS` both flag the exemption phase-out
  at 70 %), yet the number stays ≥ 0.7. The model can *list* its failure modes but does not
  *weigh* them.
- **One revision attempt on the correct answer.** On the first draw `vEiCSuG` (correct, 2574)
  ignored the format, replied in the agent's JSON, and argued from real-world 2017 figures
  ("standard deduction should be $9,350, not $4,400 which appears to be a typo") that the
  answer should change to 1832 — prior knowledge overriding the statute in the file. Any
  verification step that lets the model re-derive must guard against this.
- **Cost** is small next to the trajectory (≈ 1 extra turn, 0.6–1.5k tokens).

### 3.2 Across conditions (n = 65 per cell) ✅ 2026-10-17

- **Answer-only beats in-context on numeric discrimination, on both harnesses.** AUROC 0.35 →
  0.89 (terminus-2) and 0.54 → 0.82 (KIRA); mean numeric confidence drops from 0.86–0.90 to
  0.46–0.68 and ECE roughly halves. Without the trajectory in view the model cannot be anchored
  by its own reasoning, so it re-solves the case and reports disagreement as low confidence.
  Selective accuracy at τ = 0.9: terminus-2 0.11 → 0.33, KIRA 0.32 → 0.46, at coverage 0.43 /
  0.63. This is the first signal in the project that a verification pass can separate right
  from wrong numeric answers 🔶 (small n). Per the 2026-10-21 decision this is read as a
  *verification method*, not as a confidence baseline; see §4.
- **It is expensive, because it is a second solve.** ~8k thinking tokens per trial versus ~1k
  for in-context, and the 8k budget was not enough for 40 of 70 numeric answers. Any
  "answer-only verifier" in the harness has to budget for a full re-derivation; this is the
  cost side of the break-even analysis in [05](05_confidence_proposal.md) §3.3.
- **Binary is saturated either way.** Accuracy 0.87 on both harnesses, 3–4 errors, every
  condition states 0.86–0.95 on nearly everything; AUROCs 0.38–0.69 are noise over ≤ 4
  negatives. Verbalised confidence has nothing to work with on SARA-Binary for this model.
- **In-context on KIRA is anchored like on terminus-2.** Numeric mean confidence 0.90 at
  accuracy 0.31, AUROC 0.54; seeing the full statute (KIRA's 30 KB limit) does not make the
  model's self-assessment any better, only its answers.
- **Answer-only binary on KIRA abstains in 4 trials** (coverage 0.87 at every threshold): the
  re-derivation disagreed with the trajectory's label but the trajectory was right in 3 of
  the 4, so on binary the second solve is no more reliable than the first.

## 4. Answer-only is a verifier, not a baseline ✅ 2026-10-21

Raised by the owner on review: the answer-only prompt hands the judge the full 27 KB statute,
which a confidence module living inside the harness might not "deserve". Resolution:

- **It is not label leakage.** The statute is `/app/statute.txt` in the task container; the
  agent can `cat` it at any time, and a verifier that is part of the harness reads it with the
  same permission. The gold label is never shown. So answer-only is a legitimate harness
  component, but a *different* one: an independent re-solve of the case, not the agent's
  self-assessment.
- **The gain is from de-anchoring, not from extra statute.** KIRA's agent already saw the whole
  statute (30 KB limit) and answer-only still lifts its numeric AUROC 0.54 → 0.82. On stock
  terminus-2 the two effects are confounded (the agent saw 10 KB, the judge 27 KB), so the
  0.35 → 0.89 jump there should not be quoted as a pure de-anchoring effect.
- **Consequences for the thesis.** The confidence-calibration baseline is in-context (§2.1).
  Answer-only moves to the verification chapter as the first candidate verifier, scored on
  AUROC / selective accuracy, with its ~8k-token re-derivation charged in the break-even
  analysis ([05](05_confidence_proposal.md) §3.3).

## 5. Open items

- **In-context re-solve** 🔶 planned: keep the trajectory in view but instruct the model to
  re-derive from the statute without relying on its earlier reasoning, then state confidence.
  Tests whether de-anchoring can happen in place (no second context); if AUROC rises it is the
  cheapest verifier design, if not, the trajectory's presence is itself the anchor.
- **Prompt parity**: align the risk-list sentence across conditions before any rerun (§1).
- **Answer-step confidence** (`lastturn`, direct `joint` / `posthoc`): queued 2026-10-23
  (`~/vllm_logs/lastturn_queue.log`): t2fix 64 trials, KIRA 65, direct 65 + 258. Smoke (8 + 4
  trials): format followed in 11/12, one terminus-2 reply ignored the request and asked for
  another `grep` (the script re-draws unparsed replies, max 3). Results go in §2.3.
- **t2fix trajectories**: `incontext` / `answeronly` not run on them (only `lastturn`).
- **Repeated draws**: every number above is one sample at the server's default temperature;
  3–5 draws per trial would give per-trial variance for the numeric AUROC.
