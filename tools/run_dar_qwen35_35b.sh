#!/usr/bin/env bash
# Run the DAR harness (harbor-deonticbench) against the local vLLM server started by
# tools/serve_qwen35_35b.sh. Run from the repo root.
#
#   tools/run_dar_qwen35_35b.sh <framing> [extra harbor args...]
#   framing: direct | zeroshot | fewshot
#
# Examples:
#   tools/run_dar_qwen35_35b.sh direct --n-tasks 2 --job-name smoke   # smoke test
#   tools/run_dar_qwen35_35b.sh direct                                  # full 173 tasks
#
# Settings (decided 2026-10-15, see claude_docs/05 decision log):
#   agent terminus-2, 4 concurrent trials, agent timeout 600 s x 3,
#   64k context split as 49,152 input + 16,384 output tokens per turn.
set -euo pipefail

FRAMING="${1:?framing: direct | zeroshot | fewshot}"; shift || true
MODEL_INFO='{"max_input_tokens": 49152, "max_output_tokens": 16384, "max_tokens": 65536, "input_cost_per_token": 0.0, "output_cost_per_token": 0.0}'

cd "$(dirname "$0")/../harbor-deonticbench"
exec sg docker -c "$HOME/.local/bin/uv run harbor run \
  --path datasets/deonticbench-$FRAMING \
  --agent terminus-2 \
  --model hosted_vllm/qwen3.5-35b-a3b \
  --ak api_base=http://172.17.0.1:9009/v1 \
  --ak llm_kwargs='{\"api_key\": \"token-abc123\"}' \
  --ak model_info='$MODEL_INFO' \
  --n-concurrent 4 \
  --agent-timeout-multiplier 3 \
  --jobs-dir jobs/qwen3.5-35b-a3b/$FRAMING \
  $*"
