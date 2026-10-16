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

Pending (runs in progress 2026-10-16).

## 3. Observations

Pending.
