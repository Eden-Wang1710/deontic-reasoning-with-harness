# 08 · Self-Verbalised Confidence on Native Trajectories

- **Date opened**: 2026-10-16
- **Purpose**: the first confidence baseline of the proposal ([05](05_confidence_proposal.md)
  §4.3): after the agent has marked a task complete, ask the *same* model for the probability
  that its final answer is exactly correct, and score that number with the calibration metrics
  of [05](05_confidence_proposal.md) §3.2. Everything later (rubric-derived confidence, Prolog
  verification) is compared against this.
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
| Elicitation | "…estimate how likely it is that this answer is exactly correct … The grader uses exact match … Consider whether you may have misread or misapplied a provision, missed a cross-reference or an exception, misjudged filing status or a dependency, or made an arithmetic slip. Reply in plain text, not JSON … first line exactly `Confidence: <0–100>%` followed by at most three sentences naming the main risk." (full text in the script) |
| Label | DAR's verifier reward (exact match), from `verifier/test-stdout.txt`. |
| Metrics | ECE (10 equal-width bins), the confidence-weighted Kuiper measure as transcribed in [05](05_confidence_proposal.md) §3.2, Brier, AUROC (correct vs. wrong), and selective accuracy / coverage at confidence ≥ 0.5 / 0.7 / 0.9. Reliability table with 5 bins. |

Parsing: `Confidence: NN%` on the first line; fallbacks: any `NN%`, then a bare probability;
unparsed answers are reported and excluded from the metrics.

## 2. Results

### 2.1 terminus-2 × direct, in-context (`outputs/verbalized_confidence/t2_incontext`) ✅ 2026-10-16

65/65 parsed (one trial answered in terminus-2's JSON format without a number on the first
draw and was re-asked; a bare "15%" tax rate in that reply is why the parser only accepts
percentages tied to a confidence word). Mean extra cost per trial: 650 completion tokens
(binary) / 1,490 (numeric), ~1 min each.

| Subset | n | Accuracy | Mean confidence | ECE | Kuiper | Brier | AUROC | Coverage / accuracy at conf ≥ 0.9 |
|---|---|---|---|---|---|---|---|---|
| all | 65 | 0.492 | 0.896 | 0.404 | 0.358 | 0.398 | 0.713 | 0.69 / 0.58 |
| sara-binary | 30 | 0.867 | 0.935 | 0.085 | 0.075 | 0.116 | 0.688 | 0.90 / 0.89 |
| sara-numeric | 35 | 0.171 | 0.863 | 0.691 | 0.610 | 0.639 | **0.351** | 0.51 / 0.11 |

Reliability (5 bins): binary — [0.8, 1.0] n = 29, conf 0.94, acc 0.86; numeric — [0.6, 0.8)
n = 7, conf 0.74, acc 0.43; [0.8, 1.0] n = 28, conf 0.89, acc 0.11. Stated values are coarse:
binary answers are 95 % in 22 of 30 cases; numeric answers use 70/75/80/85/90/95 only.

### 2.2 Other conditions

Pending (terminus-2 answer-only, KIRA in-context, KIRA answer-only running 2026-10-16).

## 3. Observations (terminus-2 in-context, n = 65) ✅ 2026-10-16

- **Numeric confidence is not just miscalibrated, it is anti-correlated with correctness.**
  AUROC 0.35 < 0.5: wrong numeric answers get *higher* stated confidence (mean 0.87) than
  right ones (0.83). Thresholding at 0.9 keeps 51 % of the numeric trials at 11 % accuracy,
  below the unfiltered 17 %. As an abstention signal, in-context verbalised confidence on
  SARA-Numeric is worse than a coin for this model; the break-even analysis of
  [05](05_confidence_proposal.md) §3.3 will show it costs more than accepting everything 🔶.
- **Binary is roughly honest but uninformative.** Mean confidence 0.94 vs. accuracy 0.87 (ECE
  0.085); all four misses sit at 0.85–0.95 among 26 hits at the same values. AUROC 0.69 comes
  from a handful of lower values on correct answers, not from flagged errors.
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
