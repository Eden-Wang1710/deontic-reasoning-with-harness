#!/usr/bin/env bash
# Run the DAR harness (harbor-deonticbench) against the local vLLM server started by
# tools/serve_qwen35_35b.sh. Run from the repo root.
#
#   tools/run_dar_qwen35_35b.sh <framing> [extra harbor args...]
#   framing: direct | zeroshot | fewshot
#
# AGENT (default "t2") selects the harness:
#   t2   = Harbor's built-in terminus-2 (DAR main line), jobs under jobs/<model>/<framing>/
#   t2fix = terminus-2 with a truthful output-truncation marker (tools/dar_terminus2_local.py;
#          same 10,000-byte limit, but the "N interior bytes omitted" count is no longer
#          overwritten by a second truncation pass; decision 2026-10-16 in claude_docs/04 §8),
#          jobs under jobs/<model>/t2fix-<framing>/.
#   kira = meta-harness baseline_kira (terminus-2 subclass with native tool calling, the agent
#          behind the paper's KIRA numbers) via tools/dar_kira_local.py, which only normalises
#          the shape of the `commands` tool argument; jobs under jobs/<model>/kira-<framing>/.
#          Needs the vLLM server started with tool calling enabled (tools/serve_qwen35_35b.sh
#          does this). baseline_kira calls litellm directly and ignores llm_kwargs, so the api key
#          goes through HOSTED_VLLM_API_KEY instead.
#   t2verify = t2fix plus a rubric-based verifier sub-agent consulted at every completion claim
#          (tools/dar_terminus2_verifier.py + tools/skills/rubric_verifier/SKILL.md; design in
#          claude_docs/08_solver_verifier_harness.md), jobs under jobs/<model>/t2verify-<framing>/.
#          Tunables via --ak: max_verify_rounds=3 verifier_max_steps=8 include_reasoning=false.
#   t2verify2 = run-2 configuration of the same agent (claude_docs/08 §8): solver writes a
#          three-section answer file (derivation_record=true, tools/templates/), no confidence
#          field (report_confidence=false), verifier gets a trajectory index and reads the full
#          trajectory from a file in the container (packet_mode=index), skill v2
#          (tools/skills/rubric_verifier_v2/SKILL.md). Jobs under the same t2verify-<framing>/.
#   t2verify3 = run-3 configuration (claude_docs/08 §9): answer file lists section numbers only
#          (tools/templates/terminus-json-plain-derivation-v2.txt), skill v3 with a fixed
#          four-step plan (verifier_max_steps=4), N_CONCURRENT defaults to 2.
#
# Examples:
#   tools/run_dar_qwen35_35b.sh direct --n-tasks 2 --job-name smoke   # smoke test
#   AGENT=kira tools/run_dar_qwen35_35b.sh direct                       # baseline_kira harness
#   AGENT=t2fix tools/run_dar_qwen35_35b.sh direct                      # terminus-2, truthful marker
#   AGENT=t2verify tools/run_dar_qwen35_35b.sh direct                   # t2fix + rubric verifier
#   tools/run_dar_qwen35_35b.sh direct                                  # SARA numeric + binary (65 tasks)
#   SUBSET=all tools/run_dar_qwen35_35b.sh direct                       # all 173 tasks
#
# SUBSET (default "sara") selects subdomains by task-name glob:
#   sara = sara-numeric + sara-binary (decision 2026-10-15: only SARA for now), numeric / binary =
#   one SARA subdomain (smoke tests), all = no filter
#
# Settings (decided 2026-10-15, see claude_docs/04 decision log):
#   agent terminus-2, 4 concurrent trials, agent timeout 600 s x 3,
#   64k context split as 49,152 input + 16,384 output tokens per turn.
set -euo pipefail

FRAMING="${1:?framing: direct | zeroshot | fewshot}"; shift || true
SUBSET="${SUBSET:-sara}"
case "$SUBSET" in
  sara) INCLUDE="--include-task-name '*-sara-numeric-*' --include-task-name '*-sara-binary-*'" ;;
  numeric) INCLUDE="--include-task-name '*-sara-numeric-*'" ;;
  binary)  INCLUDE="--include-task-name '*-sara-binary-*'" ;;
  all)  INCLUDE="" ;;
  *) echo "unknown SUBSET=$SUBSET (sara|numeric|binary|all)" >&2; exit 1 ;;
esac
MODEL_INFO='{"max_input_tokens": 49152, "max_output_tokens": 16384, "max_tokens": 65536, "input_cost_per_token": 0.0, "output_cost_per_token": 0.0}'
AGENT="${AGENT:-t2}"
case "$AGENT" in
  t2)   AGENT_ARGS="--agent terminus-2"; JOBS="jobs/qwen3.5-35b-a3b/$FRAMING" ;;
  t2fix) AGENT_ARGS="--agent-import-path dar_terminus2_local:Terminus2TruthfulMarker"
        JOBS="jobs/qwen3.5-35b-a3b/t2fix-$FRAMING"
        TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
        export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}" ;;
  t2verify) AGENT_ARGS="--agent-import-path dar_terminus2_verifier:Terminus2RubricVerifier"
        JOBS="jobs/qwen3.5-35b-a3b/t2verify-$FRAMING"
        TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
        export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}" ;;
  t2verify2) TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
        AGENT_ARGS="--agent-import-path dar_terminus2_verifier:Terminus2RubricVerifier \
          --ak derivation_record=true --ak report_confidence=false --ak packet_mode=index \
          --ak skill_path=$TOOLS_DIR/skills/rubric_verifier_v2/SKILL.md"
        JOBS="jobs/qwen3.5-35b-a3b/t2verify-direct"
        export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}" ;;
  t2verify3) TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
        AGENT_ARGS="--agent-import-path dar_terminus2_verifier:Terminus2RubricVerifier \
          --ak derivation_record=true --ak report_confidence=false --ak packet_mode=index \
          --ak derivation_template=$TOOLS_DIR/templates/terminus-json-plain-derivation-v2.txt \
          --ak skill_path=$TOOLS_DIR/skills/rubric_verifier_v3/SKILL.md --ak verifier_max_steps=4"
        JOBS="jobs/qwen3.5-35b-a3b/t2verify-direct"
        N_CONCURRENT="${N_CONCURRENT:-2}"
        export PYTHONPATH="$TOOLS_DIR${PYTHONPATH:+:$PYTHONPATH}" ;;
  kira) AGENT_ARGS="--agent-import-path dar_kira_local:AgentHarness"   # tools/dar_kira_local.py wraps agents.baseline_kira
        JOBS="jobs/qwen3.5-35b-a3b/kira-$FRAMING"
        TOOLS_DIR="$(cd "$(dirname "$0")" && pwd)"
        export PYTHONPATH="$TOOLS_DIR:meta-harness/reference_examples/terminal_bench_2${PYTHONPATH:+:$PYTHONPATH}"
        export HOSTED_VLLM_API_KEY=token-abc123 ;;
  *) echo "unknown AGENT=$AGENT (t2|t2fix|t2verify|t2verify2|t2verify3|kira)" >&2; exit 1 ;;
esac
N_CONCURRENT="${N_CONCURRENT:-4}"

cd "$(dirname "$0")/../harbor-deonticbench"
exec sg docker -c "$HOME/.local/bin/uv run harbor run \
  --path datasets/deonticbench-$FRAMING \
  $AGENT_ARGS \
  --model hosted_vllm/qwen3.5-35b-a3b \
  --ak api_base=http://172.17.0.1:9009/v1 \
  --ak llm_kwargs='{\"api_key\": \"token-abc123\"}' \
  --ak model_info='$MODEL_INFO' \
  --n-concurrent $N_CONCURRENT \
  --agent-timeout-multiplier 3 \
  --jobs-dir $JOBS \
  $INCLUDE \
  $*"
