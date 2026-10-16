#!/usr/bin/env bash
# Serve Qwen3.5-35B-A3B with vLLM on 0238 (DGX Spark, GB10, 119 GB unified memory).
# Harbor task containers reach it at http://172.17.0.1:9009/v1 (Docker bridge gateway).
#
# Settings (decided 2026-10-15, see claude_docs/05 decision log):
#   bf16 weights, thinking mode ON, reasoning parser splits <think> into reasoning_content,
#   64k context, sampling defaults from the model's generation_config.json
#   (temperature 1.0, top_p 0.95, top_k 20 = Qwen's "thinking mode, general tasks" recommendation).
#   Native tool calling enabled (qwen3_xml parser) for the meta-harness baseline_kira agent,
#   which passes `tools` and no max_tokens; max_new_tokens 16384 is the server-side default
#   for such requests (terminus-2 sends its own max_tokens, unaffected). Added 2026-10-16.
set -euo pipefail

MODEL_DIR="${MODEL_DIR:-$HOME/models/Qwen3.5-35B-A3B}"
PORT="${PORT:-9009}"
LOG="${LOG:-$HOME/vllm_logs/qwen35-35b-a3b.log}"
PY="$HOME/miniconda3/envs/myenv/bin"

export HF_HUB_OFFLINE=1
export CUDA_HOME=/usr/local/cuda
export PATH="$PY:$CUDA_HOME/bin:$PATH"   # ninja lives in the conda env
# FlashInfer JIT-compiles its CUTLASS fused-MoE kernel on first start (needs ninja + nvcc,
# several minutes). Keep the compile cache in a stable place, not a per-session dir.
export XDG_CACHE_HOME="$HOME/.cache"
export MAX_JOBS=4   # cap ninja parallelism: each nvcc/cicc takes ~8 GB RAM next to the loaded model
exec "$PY/vllm" serve "$MODEL_DIR" \
  --served-model-name qwen3.5-35b-a3b \
  --host 0.0.0.0 --port "$PORT" \
  --api-key token-abc123 \
  --dtype bfloat16 \
  --max-model-len 65536 \
  --max-num-seqs 8 \
  --gpu-memory-utilization 0.75 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --override-generation-config '{"max_new_tokens": 16384}' \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  2>&1 | tee -a "$LOG"
