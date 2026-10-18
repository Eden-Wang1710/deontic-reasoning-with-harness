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
| Tool calling | `--enable-auto-tool-choice --tool-call-parser qwen3_xml` (added 2026-10-16 for the KIRA harness; terminus-2 does not send `tools`). `--override-generation-config '{"max_new_tokens": 16384}'` gives requests that omit `max_tokens` (baseline_kira) the same 16k per-turn cap as terminus-2. |
| Sampling | Server defaults from `generation_config.json`: temperature 1.0, top_p 0.95, top_k 20. **But both harnesses send `temperature: 0.7`** (Terminus2's constructor default, inherited by baseline_kira; confirmed in `episode-N/debug.json`, ✅ 2026-10-16) and no `max_tokens`, so the agent runs used T 0.7 with the server's top-p/top-k. Only the verbalised-confidence calls ([08](08_verbalized_confidence.md)) use T 1.0. Earlier text here claiming T 1.0 for the runs was wrong. |
| Context | `--max-model-len 65536`, `--max-num-seqs 8`, `--gpu-memory-utilization 0.75` (unified memory; leaves ~30 GB for OS and task containers). |
| Endpoint | `http://0.0.0.0:9009/v1`, served model name `qwen3.5-35b-a3b`, api key `token-abc123` (dummy, local only). Containers use `http://172.17.0.1:9009/v1`. |
| Memory estimate 🔶 | weights 72 GB + runtime ~8 GB + KV cache 20 KB/token for the 10 full-attention layers (1.34 GB per 64k sequence) ≈ 90 GB in use. |

### 1.2 Harness configuration ✅ 2026-10-15

Launcher: `tools/run_dar_qwen35_35b.sh <framing>`.

| Item | Value | Why |
|---|---|---|
| Agent | `terminus-2` | DAR main line |
| Model name | `hosted_vllm/qwen3.5-35b-a3b` | Harbor's `hosted_vllm` path validates `model_info` and routes to `api_base` |
| `model_info` | max_input 49,152 / max_output 16,384 / max 65,536 tokens, zero cost | terminus-2 uses these only to decide when to summarise context; it does **not** send `max_tokens` (✅ debug.json), so per-turn output was bounded by the server (max-model-len − prompt during `sara-t2-run1`; 16,384 default since the 2026-10-16 restart) |
| `--n-concurrent` | 4 | Number of trials (task containers + agent loops) running in parallel; vLLM batches their requests. MoE with 3B active is bandwidth-bound, so 4-way batching is nearly free. |
| Agent timeout | task default 600 s × `--agent-timeout-multiplier 3` = 1,800 s | Local throughput is far below the cloud APIs the paper used; 600 s would inflate timeouts. The 600 s accuracy can be recomputed post hoc from trajectory timestamps. |
| Jobs dir | `harbor-deonticbench/jobs/qwen3.5-35b-a3b/<framing>/` (gitignored) | — |
| `AGENT=t2fix` | `--agent-import-path dar_terminus2_local:Terminus2TruthfulMarker` = `tools/dar_terminus2_local.py`, a Terminus2 subclass whose `_limit_output_length` is idempotent; jobs under `jobs/qwen3.5-35b-a3b/t2fix-<framing>/` | Decision 2026-10-16 (`05` §8): stock terminus-2 truncates each observation twice, so the statute arrives with a "71 interior bytes omitted" marker while 17,205 bytes are missing (§1.5). The subclass keeps the 10,000-byte limit and only makes the count truthful (verified offline on `tax-case-10/statute.txt`: once → 17,205, twice → 71, fixed → 17,205). |
| `AGENT=kira` | `--agent-import-path dar_kira_local:AgentHarness` = `tools/dar_kira_local.py`, a subclass of meta-harness `agents.baseline_kira:AgentHarness` (the paper's KIRA harness: terminus-2 with native tool calling); jobs under `jobs/qwen3.5-35b-a3b/kira-<framing>/`; api key via `HOSTED_VLLM_API_KEY` because baseline_kira calls litellm directly and ignores `llm_kwargs` | The subclass only fixes the *shape* of the `commands` tool argument (see §1.6); prompt, tools and loop are upstream's. |

### 1.3 Runs

| Date | Framing | Tasks | Job name | Status | Notes |
|---|---|---|---|---|---|
| 2026-10-15 | direct | 2 (1 numeric + 1 binary) | `smoke` | done ✅ | pipeline check; 12 min wall, 0 exceptions, mean reward 0.5 |
| 2026-10-16 | direct, **kira** | 2 | `kira-smoke` … `kira-smoke4` | done ✅ | smoke 1 crashed (tool-argument shape); smoke 2 ran 2/2 correct but with stray XML tags in every command; smoke 3: binary clean and correct, numeric looped to timeout (JSON-in-XML arguments); smoke 4 with the full wrapper: clean, binary ✅, numeric ✗ (99309 vs 102150). See §1.6. |
| 2026-10-16 | direct, **kira** | 65 (SARA numeric + binary) | `sara-kira-run1` | done ✅ | full KIRA pass, `--n-concurrent 4`, wrapper `tools/dar_kira_local.py`; 56 finished, **9 HTTP 400 exceptions** (malformed tool-call history, see §1.6 item 5) |
| 2026-10-16 | **prompt-based direct** (DeonticBench `generate_e2e.py --task direct`, no harness) | 65 (SARA numeric + binary), 4 generations each | `DeonticBench/outputs/<domain>/direct/vllm/qwen3.5-35b-a3b/` | done ✅ 2026-10-18 (paused with SIGSTOP 2026-10-16 23:10 → 2026-10-17 21:40 UTC while the timed t2fix run had the server; 35 h wall in total, ~11 h of it generating) | run 3: the paper's "direct" column; T 0.7, thinking on, `n=4`; `experiments/run_vllm.sh` could not be used (its health check ignores the API key), `tenacity` installed into `myenv` |
| 2026-10-16 | direct, **kira** | 9 (the failed ones) | `sara-kira-rerun9` | done ✅ | rerun after the history repair in `tools/dar_kira_local.py`; 9/9 finished, 0 exceptions, binary 4/4, numeric 2/5 |
| 2026-10-16 | direct, **t2fix** | 1 | `t2fix-smoke` | done ✅ (agent timeout) | the subclass works (marker read 17,347 bytes omitted), but the trial starved: the server's 8 sequence slots were held by the confidence queue and the prompt-based direct run, its request sat in vLLM's queue (`Waiting: 1`) for 29 min. Lesson: never share the server with a timed run. |
| 2026-10-17 | direct, **t2fix** | 65 (SARA numeric + binary) | `sara-t2fix-run1` | done ✅ | server otherwise idle (direct run paused with SIGSTOP), `--n-concurrent 4`; 2 h 39 min wall, 1 agent timeout (numeric `tax-case-94`, 1,800 s), 0 other exceptions |
| 2026-10-15 | direct | 65 (SARA numeric + binary) | `sara-t2-run1` | done ✅ 2026-10-16 | first full pass, `--n-concurrent 4`; 2 h 25 min wall, 0 exceptions, 0 timeouts, mean reward 0.492 |

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
- **Throughput** (bf16, thinking on): single stream 30 tok/s. A synthetic 4-stream probe gave
  only 33 tok/s aggregate, but during the real run the server log showed a steady **62–64 tok/s
  generation with 4 requests running** (prefill ~400 tok/s, KV-cache usage 3 %) ✅ 2026-10-16 —
  so 4-way concurrency roughly doubles throughput and the earlier "concurrency barely helps"
  reading was an artefact of the probe. `--n-concurrent 4` stays; raising it to 6–8 is worth a
  test 🔶. **Prefix cache hit rate stayed 0 %** throughout: vLLM does not prefix-cache this
  hybrid (linear-attention) architecture, so every agent turn re-prefills the whole prompt.
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

#### terminus-2, full SARA pass (`jobs/qwen3.5-35b-a3b/direct/sara-t2-run1`, 2026-10-15/16) ✅

65 trials, 0 exceptions, 0 agent timeouts (longest agent phase 1,534 s < 1,800 s), 2 h 25 min
job wall time at `--n-concurrent 4`. Numbers from `tools/dar_job_summary.py … --trials`.

| Subdomain | n | Correct | Accuracy | Macro-F1 | Agent wall: median / max | Trials > 600 s | Accuracy at DAR's 600 s limit | Mean completion tok / trial |
|---|---|---|---|---|---|---|---|---|
| sara-binary | 30 | 26 | **0.867** | 0.867 | 201 s / 477 s | 0 | 0.867 | 3,651 |
| sara-numeric | 35 | 6 | **0.171** | — | 676 s / 1,534 s | 20 (3 of them correct) | **0.086** (3/35) | 11,233 |

"Accuracy at 600 s" counts every trial whose agent phase exceeded DAR's default 600 s timeout as
wrong, which is what the paper's harness would have done; it is the paper-comparable figure.

Binary confusion (expected → got): Contradiction→Contradiction 13, Entailment→Entailment 13,
Contradiction→Entailment 3, Entailment→Contradiction 1. Classes are balanced (15/15) and the
four misses go mostly towards "Entailment".

Numeric error profile (29 wrong answers, relative error |got − expected| / expected):

| < 1 % | 1–5 % | 5–20 % | ≥ 20 % | Under-estimates | Over-estimates |
|---|---|---|---|---|---|
| 7 | 7 | 10 | 5 | 25 | 4 |

Half of the misses are within 5 % of the gold figure, and 25 of 29 are *below* it. No answer was
off by a rounding margin (smallest miss: 204 on 63,345), so DeonticBench's ±$1 rule would not
change a single score.

Reference point from the paper (per the GPT summary of Table 1, 🔶 not yet checked against the
PDF): Qwen3.5-35B SARA-Numeric **34.0 % prompt-based "Direct" → 11.4 % under KIRA**, with
24.9 % of open-model KIRA runs timing out. Our terminus-2 numbers (17.1 % at 1,800 s, 8.6 % at
600 s) are in the same band as the paper's harness result and well below its prompt-based
figure, i.e. the "weaker model degrades on numeric tasks under a harness" finding reproduces
qualitatively. The harness differs (terminus-2 vs. KIRA), so this is a sanity check, not a
replication; run 3 (prompt-based direct) will check the 34.0 % side.

#### terminus-2 with a truthful truncation marker (`jobs/qwen3.5-35b-a3b/t2fix-direct/sara-t2fix-run1`, 2026-10-17) ✅

Same harness, prompt, sampling (T 0.7) and 10,000-byte observation limit as `sara-t2-run1`;
the only change is `tools/dar_terminus2_local.py` (decision 2026-10-16, `05` §8): the model is
told the true number of omitted bytes (`17347 interior bytes omitted` in 68/68 statute reads)
instead of `71`. Server otherwise idle, so the timings are comparable with run 1. 65 trials,
1 agent timeout (counted wrong), 2 h 39 min wall.

| Subdomain | n | Correct | Accuracy | Macro-F1 | Agent wall: median / max | Trials > 600 s | Accuracy at 600 s | Mean completion tok / trial | Trials with a follow-up statute read |
|---|---|---|---|---|---|---|---|---|---|
| sara-binary | 30 | 27 | **0.900** | 0.900 | 210 s / 756 s | 2 | 0.867 | 3,883 | 22 (run 1: 22) |
| sara-numeric | 35 | 7 | **0.200** | — | 687 s / 1800 s | 22 | **0.086** (3/35) | 11,507 | 11 (run 1: 5) |

Per-task comparison with the stock run (same 65 tasks, one draw each):

| Subdomain | Agree | Both correct | t2fix only | Stock only |
|---|---|---|---|---|
| sara-binary | 29/30 | 26 | 1 | 0 |
| sara-numeric | 26/35 | 2 | 5 | 4 |

Reading ✅: the truthful marker changes *behaviour* (numeric trials that go back to the statute
with `grep -n`/`sed -n` rise from 5 to 11 of 35; typical reads are `grep -n "3306"`, `grep -n
'152'`, `sed -n '95,200p'`) but not the *score*: numeric 6 → 7 correct at 1,800 s, 3 → 3 at
600 s, binary 26 → 27. Among numeric trials that did re-read, 4/11 are correct versus
3/24 without (run 1: 1/5 vs 5/30), suggestive but far inside the noise at these counts 🔶. The per-task overlap (only 2 of the 7 + 6 numeric hits shared) says the
same thing as §1.6: at n = 35 a single draw per task is dominated by sampling noise, and any
t2 / t2fix / KIRA ordering needs repeated draws (`--n-attempts`). Cost of the extra reads:
median numeric agent time 676 s → 687 s, one timeout where run 1 had none.

#### KIRA, full SARA pass (`jobs/qwen3.5-35b-a3b/kira-direct/sara-kira-run1`, 2026-10-16) ✅

65 trials; 56 finished, 9 raised `BadRequestError` (vLLM HTTP 400 `Expecting value` while
rendering a malformed tool call from the history, §1.6 item 5; being rerun as
`sara-kira-rerun9`). Numbers below are over the finished trials unless stated.

| Subdomain | n finished | Correct | Accuracy (finished) | Accuracy (exceptions = wrong) | Agent wall: median / max | Trials > 600 s | Accuracy at 600 s, over all n | Mean completion tok |
|---|---|---|---|---|---|---|---|---|
| sara-binary | 26 / 30 | 22 | **0.846** (macro-F1 0.846) | 0.733 | 207 s / 652 s | 3 (all correct) | 0.633 | 3,839 |
| sara-numeric | 30 / 35 | 9 | **0.300** | 0.257 | 548 s / 1,255 s | 12 (2 correct) | 0.200 | 8,971 |

Binary confusion (expected → got): E→E 11, C→C 11, C→E 3, E→C 1 — the same "Entailment"
lean as terminus-2. Numeric misses (21): relative error < 1 %: 6, 1–5 %: 5, 5–20 %: 9,
≥ 20 %: 1; 17 of 21 under-estimate. 3 of 65 trials used `python3`.

**KIRA complete, with the 9 reruns (`sara-kira-rerun9`, 0 exceptions) merged in** ✅:
sara-binary **26/30 = 0.867** (macro-F1 0.867), sara-numeric **11/35 = 0.314**. The reruns
are a second draw for those 9 tasks under the repaired wrapper, not a re-score, so the merged
table mixes two runs of the harness 🔶; the next full KIRA pass should be run clean.

**terminus-2 vs. KIRA over all 65 tasks**: agree on 54 (29 both right, 25 both wrong); KIRA
right where terminus-2 was wrong on 8 (7 numeric), the reverse on 3. Numeric 6/35 → 11/35.
With temperature 1.0 and n = 35 this is suggestive, not significant 🔶; the paper's direction
for this model (KIRA *below* prompt-based direct) cannot be checked until run 3 exists. KIRA
numeric trials are shorter (median 548 s vs. 676 s agent time, 9k vs. 11k completion tokens)
because the marker polling removes the fixed per-command waits.

### 1.5 Observations and failure modes ✅ 2026-10-16 (n = 65, from `sara-t2-run1`)

- **Trajectory shape is uniform.** Every trial: `cat /app/statute.txt` (terminal truncates it),
  then `grep` / `sed -n` / `cat` again for the relevant section (median 1 statute-reading
  command, max 11), then `mkdir -p /app/output && echo 'Answer: …' > /app/output/answer.txt`,
  then a "let me verify my reasoning once more" turn and `task_complete`. Binary tasks take 4–10
  agent turns (median 6), numeric 3–9 (median 5). Command heads across all trials: `cat` 168,
  `echo` 73, `mkdir` 63, `grep` 55, `sed` 31, `python3` 10, plus 136 empty keystrokes (waiting
  for output).
- **Almost no tool-assisted arithmetic.** Only 8 of 65 trials ran `python3` at all; the rest
  compute the tax tables, phase-outs and percentages in the thinking block. The model does the
  work "in its head" and uses the shell only to read the statute and write the answer. This is
  the main lever a verification module could pull on numeric tasks 🔶.
- **Numeric misses are provision errors, not arithmetic slips** (two near-misses read in full;
  the rest are the owner's manual pass). Example `FuiwZyu` (off by 400 on 207,772): applied the
  §68 itemised-deduction phase-out to the personal exemption instead of §151(d)(3), zeroing the
  exemption. Example `HF3PXPD` (off by 204 on 63,345): cited a non-existent "§152(2)(A)" for the
  standard deduction and never resolved whether the father is a dependent. In both the final
  arithmetic over the chosen inputs is right. The 25-of-29 under-estimate skew is consistent with
  missed income items / over-applied deductions rather than random error 🔶.
- **Self-verification already happens, and it is empty.** The final turn is almost always a
  re-statement of the same reasoning ("Let me verify my analysis one more time") that confirms
  the answer; in `tMSxBwb` (smoke) the model did revise once, from a right-looking 102,546 to a
  wrong 102,348. There is no independent check (no re-derivation, no code, no second reading).
  This is the behaviour the rubric-based verifier in [05](05_confidence_proposal.md) §2.1 has
  to replace, and the natural place to hook a confidence estimate.
- **Token usage carries no correctness signal.** Numeric: correct 10,457 vs. wrong 11,394 mean
  completion tokens (thinking 30k vs. 33k chars); binary: 3,624 vs. 3,823. No turn hit the 16k
  output cap (max 10,011 completion tokens in one turn); max prompt 11,376 tokens, so terminus-2
  never summarised context.
- **Time is the real cost on local hardware.** Numeric trials average 12.6 min of agent time
  and 20/35 exceed DAR's 600 s limit; under the paper's timeout half of this model's numeric
  runs would be scored as failures regardless of answer quality. Any thesis table must state
  the timeout used (decision in [05](05_confidence_proposal.md) §8).
- **Harness noise**: 26 trials logged "Parser warnings: Extra text detected before JSON object"
  (the model writes prose before the JSON tool call; terminus-2 still parses it), one "No valid
  JSON object found" (recovered next turn). Six LiteLLM error footers appear in the job log
  without any message body, no trial raised an exception and every episode has exactly one
  model call, so they are harmless 🔶.
- **Where to look**: `jobs/<…>/<trial>/agent/trajectory.json` (ATIF: per-step `message`,
  `reasoning_content`, `tool_calls[].arguments.keystrokes`, `observation`, `metrics`),
  `agent/episode-N/{prompt,response}.txt`, `verifier/test-stdout.txt` (expected vs. got),
  `result.json` (timestamps, reward, exception). Per-trial table:
  `python3 tools/dar_job_summary.py harbor-deonticbench/jobs/qwen3.5-35b-a3b/direct/sara-t2-run1 --trials`.

### 1.5.1 Error taxonomy of the numeric misses, stock vs. truthful marker ✅ 2026-10-19

Method: `tools/dar_trajectory_dossiers.py` builds one dossier per numeric trial (facts, gold
Prolog comments, the statute lines that actually appeared in the model's observations, the
statute-reading commands, the final analysis and the tail of the answer-writing thought); the
57 wrong trials (29 stock `sara-t2-run1`, 28 `sara-t2fix-run1`) were then read and labelled by
hand, checking each label against the dollar gap where possible (e.g. a $1,307 gap at 39.6 %
= the $3,300 difference between the real-world and the statute's standard deduction). Labels
are not exclusive; a trial can carry several.

**What the 10 KB truncation hides.** The first 5,000 bytes end inside §151(d)(3) (line 86 of
523), the last 5,000 start at §63(c)(5) (line 404). Everything in between is invisible unless
the model re-reads: the rest of the exemption phase-out, §151(d)(5) (no exemptions 2018–2025),
all of §152 (dependents), all of §2 (surviving spouse / head of household), §3301–3306 (the
employer's FUTA tax) and §63(a)–(c)(3) including the **basic standard deduction amounts**
($3,000 / $4,400 / 200 %). Line-level coverage of the observations confirms it: in the stock
run 25/29 wrong and 5/6 right numeric trials never saw §63(c)(2); 0/35 saw §152, §2 or §3306.
With the truthful marker 11/35 trials re-read, mostly §152 and §2; still 0/35 read §3301–3306.

| Error label (what the model did) | Hidden provision | Stock, of 29 | Truthful marker, of 28 |
|---|---|---|---|
| Standard deduction amount from prior knowledge or invented (e.g. $6,350 / $12,700 / $9,350 for 2017, $6,300 / $12,600 for 2015–16, $12,200 for 2013, $5,000, $8,800, $5,800), usually with a fabricated cite (§162, §163, §153, §152(a)(2)(B)) | §63(c)(2) | 16 | 14 |
| Employer FUTA tax ignored when the case pays wages ("the agricultural labor is a red herring", "the statute only covers income tax") | §3301–3306 | 7 | 7 |
| Filing status: head of household assumed from "same abode" without the maintain-the-household cost fact, or joint return kept for a nonresident alien | §2 | 7 | 7 |
| Dependent misjudged (parent / uncle / grandchild with no income not counted, or counted when the facts do not allow it) | §152 | 4 | 4 |
| Personal exemptions kept in 2018–2019 cases (statute zeroes them) | §151(d)(5) | 2 | 3 |
| Exemption phase-out or §68 limitation mis-applied (wrong rate, not applied, AGI reduced by itemised deductions first) | §151(d)(3) half visible; §68 visible | 4 | 4 |
| Wages paid to employees deducted from gross income as a "business expense" (no basis in the statute) | — | 1 | 3 |
| Arithmetic / rounding / bracket-table slip with all inputs right (429,313 × 0.396 computed as 169,608 or 170,000 instead of 170,008; 26,566.50 rounded down; MFS bracket base read from the wrong row) | — | 2 | 3 |
| Trials with at least one hidden-provision label | | 26/29 | 24/28 |
| Trials that re-read the statute at all | | 5/29 | 11/28 |

Per-trial labels (trial suffix → labels; case number in brackets): stock — x6mWBs4 [10] SD, FUTA,
wage-deduction; dZzNJ8W [15] SD; n6a8h2b [18] SD; YEAGroo [2] SD; XmAkJeY [29] SD; cask4sJ [35]
SD, status; cEihyWr [39] status; jMBLdZH [44] status (nonresident alien); PubZBsD [47] SD, FUTA;
Sh3HGrS [49] dependent, phase-out; S4Ac8LJ [51] SD; kp4Ei3T [58] SD, FUTA; xqFtq4R [6] §68/AGI,
phase-out; KWkXWCv [60] dependent; dt5MGVb [61] SD, status, exemptions omitted; FVFNhgD [63]
2018 rule; JWoibqm [64] status, phase-out; FuiwZyu [67] arithmetic; Feqfmvy [70] bracket base;
QBB64uS [71] SD, phase-out; 74t9jGN [74] SD; PdAUjwL [75] status/dependent; N5Tkrkf [78] 2018
rule, status; xPyBa33 [83] FUTA; XUuTHgh [91] SD, FUTA; WFrmvX5 [93] SD; HF3PXPD [94] SD,
dependent, FUTA; jdkZZ8V [95] SD; oS6ZpSX [97] FUTA. Truthful marker — 3gC97sL [10] wage-
deduction, FUTA; nyTdf2U [15] SD; Q7Y6CHj [18] SD, dependent; XZpTdmh [2] rounding; QZgDsJq [29]
SD; NxBRgHY [35] SD, status, exemptions omitted; rj5AVfP [39] SD, status; kCFyz68 [44] status,
SD; fJ3hctm [47] SD, FUTA; tPm25A7 [49] SD, dependent; Q5hz4uV [56] phase-out; fw5ygme [58] SD,
wage-deduction, FUTA; zUmRLF5 [6] §68/AGI; KJbFhee [60] SD, dependent; dscEQQh [61] status;
kQKWaUU [63] 2018 rule, SD table; ANUzNqN [64] status, phase-out; QZxUYzM [67] arithmetic;
7dqgEsY [69] SD; Akyidv2 [70] bracket base, phase-out; oEza2Pz [75] status/dependent; kjKkw22
[78] 2018 rule, status; Ci4Fpgd [83] FUTA; hxGBtTM [84] SD; ihXTCRF [90] 2018 rule; snXUPmL [91]
FUTA; hy8XzSs [94] SD (2018 amount used for 2010), wage-deduction, FUTA, timeout; RqobqLo [97]
FUTA.

Reading:

- **The dominant failure is substituting memory for the hidden text, not misreading the
  visible text.** 16/29 and 14/28 misses use a real-world or invented standard deduction, and
  the model backs it with a section number that does not exist in the file. The truthful
  marker did not cure this: 13 of the 14 SD misses in the fix run issued exactly one read.
  Being told "17,347 bytes omitted" made the model look up dependents and filing status
  (§152, §2) but not amounts it believed it knew.
- **FUTA is invisible to the model in both runs** (7 + 7). No trial ever read §3301–3306, and
  several explicitly declared the wage facts a distraction. These are the cases the paper's
  "harness lets the model navigate" premise fails on: the model does not know there is
  something to navigate to.
- **Re-reading helps the sub-problem that was read, not the answer.** The fix-run trials that
  read §2/§152 got dependents right more often but still lost on FUTA or arithmetic (`3gC97sL`,
  `dscEQQh`, `ANUzNqN`); XZpTdmh [2] read everything, computed 26,566.50 and rounded the wrong
  way.
- **Pure computation errors exist but are few** (2 and 3), always in the mental multiplication
  of the top bracket or in rounding; python was used in 5 of the 57 wrong trials.
- **Implication for the verifier (05 §2.1)**: a provision-coverage check ("did the agent read
  the section its answer depends on?") would flag 26/29 and 24/28 of these misses before any
  arithmetic check, and a cheap "every dollar amount in the answer must be quoted from the file"
  rule would catch the standard-deduction class outright 🔶.

### 1.6 KIRA harness (`baseline_kira`) with a locally served Qwen ✅ 2026-10-16

`baseline_kira` is upstream's `AgentHarness` in
`harbor-deonticbench/meta-harness/reference_examples/terminal_bench_2/agents/baseline_kira.py`: a
terminus-2 subclass that replaces the JSON-in-text protocol with native tool calls
(`execute_commands{analysis, plan, commands[]}`, `task_complete`, `image_read`), polls for a
completion marker instead of sleeping a fixed duration, and calls litellm directly (900 s
per-call timeout, 5 retries, no `max_tokens`). It is the agent behind the paper's KIRA column.
It runs from the fork's own `uv` env with the meta-harness directory on `PYTHONPATH`; no
second environment is needed (its pinned PyPI `harbor==0.3.0` is not installed).

What had to change to run it against Qwen3.5-35B-A3B on vLLM:

1. **Server-side tool parsing.** vLLM must be started with `--enable-auto-tool-choice
   --tool-call-parser qwen3_xml` (Qwen3.5 emits `<tool_call><function=…><parameter=…>` XML).
   A probe with the kira tool schema returns a proper `tool_calls` entry and the thinking in
   `reasoning`; litellm exposes it as `reasoning_content`, so trajectories keep the thinking.
2. **Array-of-objects arguments come back malformed.** For `commands` (array of
   `{keystrokes, duration}`) the model nests `<parameter=keystrokes>` inside
   `<parameter=commands>`; the parser returns a single object instead of an array, with the
   inner tag left as literal text: `{"keystrokes": "\n<parameter=keystrokes>\ncat
   /app/statute.txt\n", "duration": 2.0}`. Stock `baseline_kira` crashes on the object
   (`AttributeError: 'str' object has no attribute 'get'`, `kira-smoke`, both trials, before
   the first command). After wrapping it in a list (`kira-smoke2`) both trials passed, but
   every command was preceded by a bogus `<parameter=keystrokes>` line that bash rejected
   (`syntax error near unexpected token 'newline'`), visible in every observation.
3. **JSON inside one XML parameter.** In `kira-smoke3` the numeric trial produced 134 steps
   and timed out: from the second turn on the model opened `<parameter=analysis>` and then
   continued in *JSON* style (`…", "plan": "…", "commands": {"keystrokes": …}}`), so the
   parser returned the whole thing as the `analysis` string, `commands` was missing, no
   command ever ran, and the agent looped on terminus-2's "your response contained no tool
   calls" warning while concluding "terminal output display is broken". 122 of the trial's
   122 `execute_commands` calls had this shape.
4. **Fix, outside the subtree**: `tools/dar_kira_local.py` subclasses `AgentHarness` and
   overrides `_parse_tool_calls` to (a) recover `commands` from a JSON-in-XML value by
   re-attaching the opening `{"analysis": "` and re-parsing (regex fallback on
   `"keystrokes"`), (b) accept an object, a list of objects, a list of strings or a
   JSON-encoded string under `commands`, and (c) strip stray `<parameter=…>` /
   `</parameter>` lines and collapse trailing newlines in each keystroke to one. All 122
   malformed calls from smoke 3 recover offline. Nothing else is touched.
5. **Malformed arguments in the history break the *next* request** (found in the full run,
   9/65 trials). `baseline_kira` appends the raw tool call to the chat history and sends it
   back on every later turn; vLLM json-parses `function.arguments` while rendering Qwen's chat
   template and answers HTTP 400 `Expecting value: line 1 column 898`. The wrapper now repairs
   the arguments in `_extract_tool_calls`, i.e. before they enter the history (commit
   `d0610c2`), and the 9 tasks are rerun as `sara-kira-rerun9`.
   This is a deviation from the paper's harness 🔶: the paper ran open models through
   OpenRouter / vLLM with the stock agent, so part of its reported 27.8 % "execution-type
   error" rate for open models under KIRA may be this same tool-format failure rather than
   reasoning failure (hypothesis, worth checking against the paper's Table 3 and asking the
   author).

Smoke results on the same two tasks as the terminus-2 smoke: `kira-smoke2` (stray tags, but
commands ran) sara-binary Entailment ✅ 90 s / 5 steps / 1,521 completion tokens, sara-numeric
**102150 ✅** 511 s / 11 steps / 13,250 tokens — the model first wrote 102546, called
`task_complete`, then on the confirmation prompt re-derived and corrected to 102150, the same
task terminus-2 (`tMSxBwb`) got wrong after revising in the other direction. `kira-smoke4`
(final wrapper): binary ✅ 127 s / 7 steps / 2,280 tokens, numeric ✗ 99309 in 398 s / 7 steps /
10,045 tokens, no malformed command in either. Sampling at temperature 1.0 makes single
trials noisy; no KIRA-vs-terminus-2 conclusion from n = 2.

### 1.7 Comparison with DAR Table 1 and the list of deviations ✅ 2026-10-16

The paper (`DeonticBench/docs/assets/dar_paper.pdf`, Table 1, p. 10; model row "Qwen3.5-35B" =
Qwen3.5-35B-A3B per §3.2) reports, for SARA: direct 0.340 / 0.740, terminus-2 0.229 / 0.833,
terminus-kira 0.114 / 0.829, claude-code 0.371 / 0.833 (SARA-Num accuracy / SARA-Bin macro-F1).
Each trial has a 10-minute budget and timeouts, parse failures and runtime errors count as
incorrect (p. 4). Open-source models under Terminus-KIRA had 27.8 % such errors, 24.9 %
timeouts (Table 3).

| Harness | Metric | Paper | Ours, 1,800 s limit | Ours, 600 s limit (paper rule) | Δ at 600 s, in items (n = 35 / 30) |
|---|---|---|---|---|---|
| terminus-2 | SARA-Num acc | 0.229 | 0.171 | **0.086** | −5 |
| terminus-2 | SARA-Bin macro-F1 | 0.833 | 0.867 | 0.867 | +1 |
| terminus-2, truthful marker (`t2fix`) | SARA-Num acc | (0.229) | 0.200 | **0.086** | −5 |
| terminus-2, truthful marker (`t2fix`) | SARA-Bin macro-F1 | (0.833) | 0.900 | 0.867 (2 over 600 s) | +1 |
| terminus-kira | SARA-Num acc | 0.114 | 0.314 | **0.229** | +4 |
| terminus-kira | SARA-Bin macro-F1 | 0.829 | 0.867 | 0.733 (4 timeouts) | −3 |
| direct (prompt) | SARA-Num acc | 0.340 (±1 rule) | **0.478** [0.371, 0.600] ±1 rule; **0.435** [0.343, 0.543] exact | no time limit | +5 |
| direct (prompt) | SARA-Bin macro-F1 | 0.740 | **0.822** [0.764, 0.866] (acc 0.823) | no time limit | +2 to +3 |

Reading: SARA-Binary reproduces for both harnesses (within one or two items). SARA-Numeric
does not: terminus-2 is below the paper and KIRA above it, i.e. the paper's ordering
(terminus-2 > KIRA) is reversed here. With n = 35 one item is 2.9 points, so the KIRA gap
(+4 items at 600 s, +7 at 1,800 s) is the only one worth explaining. Candidate causes, all
🔶 until tested: (a) the tool-format repairs in `tools/dar_kira_local.py` — the paper ran the
stock agent, and stock baseline_kira crashed or looped on this model in our smoke tests, so
some of the paper's 27.8 % KIRA "execution errors" may be this; (b) local throughput: the
paper's timeouts are counted as wrong and our 600 s re-scoring only approximates that (their
tokens/s is unknown); (c) sampling: ours T 0.7 top-p 0.95 top-k 20 bf16 thinking-on, theirs
unstated; (d) the 9 KIRA reruns are a second draw.

**Prompt-based direct (run 3, `tools/db_direct_score.py`, 2026-10-18) ✅.** 35 + 30 cases, 4
generations each at T 0.7 with thinking (`generate_e2e.py --task direct`, statute in the
prompt, no tools, no time limit), bootstrap = one generation per case, 1,000 replicates.
Numeric: 0.478 [0.371, 0.600] with DeonticBench's ±1 rule, 0.435 [0.343, 0.543] with DAR's
exact match; abstain 1.4 % (2 empty outputs of 140); 8 cases right in all 4 draws, 12 wrong in
all 4; majority vote 0.486, pass@4 0.657; ~12k generated tokens per answer. Binary: accuracy
0.823, macro-F1 0.822 [0.764, 0.866], no abstentions, pooled confusion Entailment→Contradiction
12/56, Contradiction→Entailment 9/64. Both are above the paper's direct column (0.340 / 0.740)
by 2–5 items; the ±1 vs. exact gap is 1.5 items (🔶 why we exceed the paper: sampling settings
and the exact model build are not stated there). The comparison that matters is within our own
runs: the same model on the same cases goes from 0.44 (prompt, exact rule) to 0.17 (terminus-2),
0.20 (t2fix) and 0.31 (KIRA) at 1,800 s, with the same ~11–12k thinking tokens per answer, so
the DAR degradation finding reproduces here and is larger than in the paper (0.340 → 0.229 /
0.114). Binary moves the other way (0.82 prompt → 0.87–0.90 harness), as in the paper.

**Deviations and gotchas, per harness.** Everything we changed or found that could move a
number. "Stock" = unchanged upstream behaviour that still matters when reading results.

terminus-2 (`sara-t2-run1`):

1. *Stock, important:* **the model never sees the whole statute.** `cat /app/statute.txt`
   (27,205 bytes) is cut by `_limit_output_length(max_bytes=10000)` to the first 5,000 and
   last 5,000 bytes, and the limiter runs twice (once on the command output, once when the
   prompt is built), so the second pass only removes the first pass's marker and the
   observation claims "71 interior bytes omitted" when 17,205 are gone ✅ (checked on all 65
   trials: all truncated, identical marker). What the model saw: §1 and §151(a)–(d)(3)(B),
   then a jump to the end of §63(c)(5), §68 and §7703. **Never shown in that read: §2 (head
   of household / surviving spouse), §152 (dependents), §3301/§3306, most of §63 (standard
   deduction)** — exactly the provisions behind the numeric misses in §1.5. Only 27/65 trials
   issued a follow-up targeted read (`grep` / `sed -n`); 38 answered from the truncated view.
   The paper's terminus-2 runs used the same code, so this is part of what its numbers measure.
2. *Stock:* temperature 0.7 and no `max_tokens` (see §1.1), thinking on, thinking of earlier
   turns not in the history.
3. *Stock:* 25/65 trials logged "Parser warnings: Extra text detected before JSON object"
   (prose before the JSON; recovered), 1 "No valid JSON object found" (one wasted turn).
4. *Ours:* agent timeout 600 s × 3; we report both limits. 20/35 numeric trials exceed 600 s.
5. *Ours:* `--n-concurrent 4` on one GPU; affects wall time only.
6. *Ours (serving):* bf16, vLLM 0.20.1 on GB10, `--reasoning-parser qwen3`; prefix caching
   inactive for this hybrid architecture (correctness unaffected).

KIRA (`sara-kira-run1` + `sara-kira-rerun9`):

1. *Stock, important:* **output limit raised to 30,000 bytes** (`baseline_kira._limit_output_length`),
   so the full statute fits in one `cat` ✅ (74/74 statute reads untruncated). This is the
   largest *content* difference between the two harnesses on SARA and the most plausible reason
   KIRA beats terminus-2 on numeric here 🔶; it also means "terminus-2 vs. KIRA" on SARA is
   partly "truncated statute vs. full statute".
2. *Stock:* native tool calls (`execute_commands` / `task_complete` / `image_read`), marker
   polling instead of fixed waits (shorter wall time), litellm called directly with
   `timeout 900 s` and 5 retries, `temperature 0.7`, no `max_tokens`.
3. *Ours (serving):* `--enable-auto-tool-choice --tool-call-parser qwen3_xml`; server-side
   default `max_new_tokens 16384` for requests without `max_tokens`.
4. *Ours (wrapper `tools/dar_kira_local.py`)*, needed because Qwen3.5 via `qwen3_xml` does not
   follow the tool schema (§1.6): `commands` object → list; stray `<parameter=…>` tags and
   extra trailing newlines removed from keystrokes; JSON-written-inside-an-XML-parameter
   recovered (122/122 such calls in smoke 3); the repaired arguments are also what goes into
   the chat history (HTTP 400 otherwise, 9/65 trials in run 1). Prompt, tools, loop and
   confirmation logic are untouched.
5. *Ours:* the 9 trials that hit the HTTP 400 were rerun under the fixed wrapper and merged
   (second draw for those tasks).
6. *Stock:* 33 "no tool calls" turns across 74 trials (model answered in text only; the
   harness re-prompts) — see the no-tool counts in `tools/dar_job_summary.py` output.
7. *Ours (env):* run from the fork's `uv` env with the meta-harness directory on `PYTHONPATH`,
   not from the pinned `harbor==0.3.0` PyPI env in `terminal_bench_2/pyproject.toml`.
   `HOSTED_VLLM_API_KEY` carries the key because baseline_kira ignores `llm_kwargs`.

Prompt-based direct (run 3, DeonticBench): statute fully in the prompt; `n = 4` generations at
T 0.7; DeonticBench's own scorer uses ±$1 on numeric answers, so for the DAR comparison the
outputs must be re-scored with exact match (decision 2026-10-14 in
[05](05_confidence_proposal.md) §8).
