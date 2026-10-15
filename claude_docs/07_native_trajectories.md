# 07 · Native Trajectories per Model

- **Purpose**: the log of **pass-1 ("native") trajectories** collected by running the unmodified DAR
  harness with each model, before any self-verification or confidence module is added. One section
  per model. Each section records how the model was served, how the harness was configured, what
  was run, the scores, and the failure-mode observations that feed the rubric design in
  [05](05_confidence_proposal.md) §2.1.
- **Date opened**: 2026-10-15
- **Confidence convention**: ✅ = checked directly on the machine / in the run output on the date
  given. 🔶 = expectation or inference.
- Run artifacts (`harbor-deonticbench/jobs/`, vLLM logs) are **not in git**; they live on the
  machine named in each section. Only the numbers and observations recorded here are shared.

## 0. Which runs, and why

Per the proposal ([05](05_confidence_proposal.md) §4.2), the open-weight model is
**Qwen3.5-35B-A3B**, chosen because (a) it is the model the proposal names, (b) DAR's paper reports
it (SARA-Numeric 34.0 % direct → 11.4 % KIRA, Airline 13.7 % → 1.3 %, per the GPT summary of Table 1
🔶 — re-check against the paper), so a reference number exists to validate our pipeline against, and
(c) it is the model that *degrades* under the agentic harness, which is exactly where
self-verification and confidence have room to help. Larger Qwen variants (122B-A10B, 397B) are
deferred to a later scale comparison.

**Scope decision (2026-10-15)**: for now only the two **SARA** subdomains are run (sara-numeric 35
+ sara-binary 30 = 65 tasks per framing); Airline and USCIS-AAO are deferred. SARA is the
evaluation benchmark named in the proposal ([05](05_confidence_proposal.md) §4.1), and the
break-even-price analysis needs SARA-Numeric specifically.

Planned run order for each model:

| # | Harness | Framing | Purpose |
|---|---|---|---|
| 1 | `terminus-2` (Harbor built-in) | `deonticbench-direct` | DAR's main-line harness; first trajectories |
| 2 | `baseline_kira` (meta-harness) | `deonticbench-direct` | The harness behind the paper's headline numbers |
| 3 | DeonticBench prompt-based direct (no harness) | — | Reproduce the paper's "Direct" column as an environment check |
| later | `terminus-2` | `zeroshot`, `fewshot` (Prolog) | Prolog framings, needed for the symbolic verifier |

`kira_deontic_grounded` and `kira_compute_then_answer` are not in the released code
([04](04_dar_review.md) §3) and cannot be run.

## 1. Qwen3.5-35B-A3B (self-hosted on 0238)

### 1.1 Model and serving ✅ 2026-10-15

| Item | Value |
|---|---|
| Weights | `Qwen/Qwen3.5-35B-A3B` from HF, bf16 safetensors, 71.9 GB (14 shards), at `~/models/Qwen3.5-35B-A3B` on 0238. Not gated. |
| Architecture | `Qwen3_5MoeForConditionalGeneration`: 40 layers, 10 full-attention + 30 linear-attention, 256 experts / 8 active, 2 KV heads × 256 head-dim, native context 262,144. Has a vision tower (unused). |
| Server | vLLM 0.20.1 in conda env `myenv` (torch 2.11.0+cu130, flashinfer 0.6.8), aarch64, GB10. Launcher: `tools/serve_qwen35_35b.sh`. |
| Precision | **bf16** (paper-comparable; FP8 MoE kernels on GB10 untested 🔶). |
| Thinking | **On** (model default). `--reasoning-parser qwen3` routes `<think>` into `reasoning_content` so the harness only sees the answer text. |
| Sampling | Model `generation_config.json`: temperature 1.0, top_p 0.95, top_k 20 — Qwen's "thinking mode, general tasks" recommendation. Harbor's terminus-2 default temperature is also 1.0. |
| Context | `--max-model-len 65536`, `--max-num-seqs 8`, `--gpu-memory-utilization 0.75` (unified memory; leaves ~30 GB for OS and task containers). |
| Endpoint | `http://0.0.0.0:9009/v1`, served model name `qwen3.5-35b-a3b`, api key `token-abc123` (dummy, local only). Containers use `http://172.17.0.1:9009/v1`. |
| Memory estimate 🔶 | weights 72 GB + runtime ~8 GB + KV cache 20 KB/token for the 10 full-attention layers (1.34 GB per 64k sequence) ≈ 90 GB in use. |

### 1.2 Harness configuration ✅ 2026-10-15

Launcher: `tools/run_dar_qwen35_35b.sh <framing>`.

| Item | Value | Why |
|---|---|---|
| Agent | `terminus-2` | DAR main line |
| Model name | `hosted_vllm/qwen3.5-35b-a3b` | Harbor's `hosted_vllm` path validates `model_info` and routes to `api_base` |
| `model_info` | max_input 49,152 / max_output 16,384 / max 65,536 tokens, zero cost | terminus-2 uses these to decide when to summarise context; 16k per turn is a compromise between Qwen's 32k recommendation and the time limit |
| `--n-concurrent` | 4 | Number of trials (task containers + agent loops) running in parallel; vLLM batches their requests. MoE with 3B active is bandwidth-bound, so 4-way batching is nearly free. |
| Agent timeout | task default 600 s × `--agent-timeout-multiplier 3` = 1,800 s | Local throughput is far below the cloud APIs the paper used; 600 s would inflate timeouts. The 600 s accuracy can be recomputed post hoc from trajectory timestamps. |
| Jobs dir | `harbor-deonticbench/jobs/qwen3.5-35b-a3b/<framing>/` (gitignored) | — |

### 1.3 Runs

| Date | Framing | Tasks | Job name | Status | Notes |
|---|---|---|---|---|---|
| 2026-10-15 | direct | 2 (1 numeric + 1 binary) | `smoke` | done ✅ | pipeline check; 12 min wall, 0 exceptions, mean reward 0.5 |
| 2026-10-15 | direct | 65 (SARA numeric + binary) | `sara-t2-run1` | running (started 23:36 UTC) | first full pass, `--n-concurrent 4` |

### 1.3.1 Serving facts learned from the smoke test ✅ 2026-10-15

- **First start needs a one-off kernel build.** FlashInfer JIT-compiles its CUTLASS fused-MoE
  kernel for sm_121 (90 compile units, ninja + nvcc). Done with the model loaded, 7 parallel
  `cicc` processes at ~8 GB each were OOM-killed. Fix: `~/vllm_logs/precompile_moe.sh` builds the
  kernel standalone (82 s with `MAX_JOBS=10`), and the launcher caps `MAX_JOBS=4` and pins
  `XDG_CACHE_HOME=~/.cache` so the cache survives. Later starts load it from cache.
- **Startup**: weights 1 min 45 s from NVMe; torch.compile cached; KV cache 1,015,808 tokens
  (15.5 concurrent 64k requests). ~93 GB of the 119 GB in use while serving.
- **Reasoning split works**: with `--reasoning-parser qwen3` the OpenAI response carries the
  thinking in `message.reasoning` (vLLM 0.20 field name; **not** `reasoning_content`) and the
  answer in `content`. Harbor stores the thinking per step as `reasoning_content` in
  `agent/trajectory.json`.
- **Throughput** (bf16, thinking on): single stream 30 tok/s; 4 concurrent streams 33 tok/s
  aggregate (8 tok/s each). Concurrency barely raises aggregate throughput on this machine 🔶
  (bandwidth-bound unified memory, or the GDN linear-attention / MoE kernels not batching well);
  it does not hurt either, so `--n-concurrent 4` stays. Total run time is therefore ≈ total
  generated tokens / 33 tok/s.
- **Thinking is long.** A trivial arithmetic prompt produced ~1k thinking tokens; a one-sentence
  statute question hit the 2,048-token cap still thinking.

### 1.4 Results

#### Smoke test (`jobs/qwen3.5-35b-a3b/direct/smoke`, 2026-10-15) ✅

| Trial | Expected | Got | Reward | Wall | Agent steps | Prompt tok | Completion tok | Thinking chars |
|---|---|---|---|---|---|---|---|---|
| sara-binary (§2(b)(2)(C), Bob 2014) | Entailment | Entailment | 1.0 | 175 s | 6 | 28,863 | 2,358 | 3,939 |
| sara-numeric (Alice 2015 tax) | 102150 | 102348 | 0.0 | 729 s | 6 | 27,019 | 18,562 | 53,921 |

Both completed without exception or timeout. Time estimate for the full SARA pass 🔶: numeric
≈ 35 × 12 min and binary ≈ 30 × 3 min of generation, with concurrency adding little, ≈ 6–8 h.

#### Full SARA pass (`sara-t2-run1`)

Pending.

### 1.5 Observations and failure modes

From the smoke test only (n = 2; to be rewritten once the full pass is in):

- **Trajectory shape**: `cat /app/statute.txt` first (output truncated by the terminal), then
  `grep`/`sed -n` to the relevant section, then `echo 'Answer: …' > /app/output/answer.txt`,
  then `mark_task_complete` with the harness's confirmation prompt. The binary case was
  resolved in 6 steps with short thinking.
- **Numeric case**: same 6 steps, but one turn carried most of the 18.5k completion tokens — a
  very long in-head computation after reading the statute. The answer was off by 198 on a
  102k figure, i.e. a computation or provision-application slip, not a format or tool failure.
  Which provision was misapplied is to be read from the trajectory.
- **Where to look**: `jobs/<…>/<trial>/agent/trajectory.json` (ATIF: per-step `message`,
  `reasoning_content`, `tool_calls`, `observation`, `metrics`), `agent/episode-N/{prompt,response}.txt`,
  `verifier/test-stdout.txt` (expected vs. got), `result.json` (timestamps, reward, exception).
