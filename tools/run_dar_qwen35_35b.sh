#!/usr/bin/env bash
# Run the DAR harness (harbor-deonticbench) against the local vLLM server started by
# tools/serve_qwen35_35b.sh. Run from the repo root.
#
#   tools/run_dar_qwen35_35b.sh <framing> [extra harbor args...]
#   framing: direct | zeroshot | fewshot
#
# AGENT (default "t2") selects the harness:
#   t2   = Harbor's built-in terminus-2 (DAR main line), jobs under jobs/<model>/<framing>/
#   kira = meta-harness baseline_kira (terminus-2 subclass with native tool calling, the agent
#          behind the paper's KIRA numbers), jobs under jobs/<model>/kira-<framing>/.
#          Needs the vLLM server started with tool calling enabled (tools/serve_qwen35_35b.sh
#          does this). baseline_kira calls litellm directly and ignores llm_kwargs, so the api key
#          goes through HOSTED_VLLM_API_KEY instead.
#
# Examples:
#   tools/run_dar_qwen35_35b.sh direct --n-tasks 2 --job-name smoke   # smoke test
#   AGENT=kira tools/run_dar_qwen35_35b.sh direct                       # baseline_kira harness
#   tools/run_dar_qwen35_35b.sh direct                                  # SARA numeric + binary (65 tasks)
#   SUBSET=all tools/run_dar_qwen35_35b.sh direct                       # all 173 tasks
#
# SUBSET (default "sara") selects subdomains by task-name glob:
#   sara = sara-numeric + sara-binary (decision 2026-10-15: only SARA for now), all = no filter
#
# Settings (decided 2026-10-15, see claude_docs/05 decision log):
#   agent terminus-2, 4 concurrent trials, agent timeout 600 s x 3,
#   64k context split as 49,152 input + 16,384 output tokens per turn.
set -euo pipefail

FRAMING="${1:?framing: direct | zeroshot | fewshot}"; shift || true
SUBSET="${SUBSET:-sara}"
case "$SUBSET" in
  sara) INCLUDE="--include-task-name '*-sara-numeric-*' --include-task-name '*-sara-binary-*'" ;;
  all)  INCLUDE="" ;;
  *) echo "unknown SUBSET=$SUBSET (sara|all)" >&2; exit 1 ;;
esac
MODEL_INFO='{"max_input_tokens": 49152, "max_output_tokens": 16384, "max_tokens": 65536, "input_cost_per_token": 0.0, "output_cost_per_token": 0.0}'
AGENT="${AGENT:-t2}"
case "$AGENT" in
  t2)   AGENT_ARGS="--agent terminus-2"; JOBS="jobs/qwen3.5-35b-a3b/$FRAMING" ;;
  kira) AGENT_ARGS="--agent-import-path agents.baseline_kira:AgentHarness"
        JOBS="jobs/qwen3.5-35b-a3b/kira-$FRAMING"
        export PYTHONPATH="meta-harness/reference_examples/terminal_bench_2${PYTHONPATH:+:$PYTHONPATH}"
        export HOSTED_VLLM_API_KEY=token-abc123 ;;
  *) echo "unknown AGENT=$AGENT (t2|kira)" >&2; exit 1 ;;
esac

cd "$(dirname "$0")/../harbor-deonticbench"
exec sg docker -c "$HOME/.local/bin/uv run harbor run \
  --path datasets/deonticbench-$FRAMING \
  $AGENT_ARGS \
  --model hosted_vllm/qwen3.5-35b-a3b \
  --ak api_base=http://172.17.0.1:9009/v1 \
  --ak llm_kwargs='{\"api_key\": \"token-abc123\"}' \
  --ak model_info='$MODEL_INFO' \
  --n-concurrent 4 \
  --agent-timeout-multiplier 3 \
  --jobs-dir $JOBS \
  $INCLUDE \
  $*"
