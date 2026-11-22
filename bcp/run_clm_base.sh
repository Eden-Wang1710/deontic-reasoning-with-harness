#!/usr/bin/env bash
# The baseline of claude_docs/12 §4: the released CLM agent (ClmAgent, installed from the CLM
# repository at commit 3c89408 into ~/envs/clm) with its configs/bcp.yaml, on our rebuilt
# BrowseComp-Plus tasks, with Qwen3.5-9B on the local vLLM server.
#
#   bcp/run_clm_base.sh <set> <job-name> [extra harbor args...]
#   bcp/run_clm_base.sh smoke5 clm-base-smoke
#   bcp/run_clm_base.sh dev50  clm-base-dev50
#
# It starts the tools server and the model server (bcp/servers.sh), runs the job, stops both
# servers, then starts the judge model, grades the answer files and stops it (bcp/judge.py).
# Everything it writes goes under <data>/jobs/, outside the repository: trajectories and judge
# outputs contain the decrypted questions.
#
# Differences from the released bcp.yaml: max_steps 100 (the paper's setting; the file ships
# 500) and flops_model_key 9b (ships 27b). Everything else is the released file.
# Environment: N_CONCURRENT (16), MAX_STEPS (100), KEEP_SERVERS=1 (leave the agent servers
# running and skip the judge), JUDGE=0 (skip the judge).
set -euo pipefail
SET="${1:?set: smoke5 | dev50 | dev150 | dev150-50 | first<N>}"; JOB="${2:?job name}"; shift 2
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${BCP_DATA:-$HOME/data/browsecomp-plus}"
N_CONCURRENT="${N_CONCURRENT:-16}"
MAX_STEPS="${MAX_STEPS:-100}"
export PATH="$HOME/envs/clm/bin:$PATH"
export OPENAI_API_KEY=token-abc123   # the local vLLM server's fixed key, not a secret

[ -d "$DATA/tasks/$SET" ] || python3 "$REPO/bcp/build_tasks.py" tasks --set "$SET"
[ -e "$DATA/jobs/$JOB" ] && { echo "$DATA/jobs/$JOB exists" >&2; exit 1; }

cleanup() { [ "${KEEP_SERVERS:-0}" = 1 ] || "$REPO/bcp/servers.sh" stop; }
trap cleanup EXIT
[ -f "$DATA/run/vllm.pid" ] || MAX_NUM_SEQS="$N_CONCURRENT" "$REPO/bcp/servers.sh" start "$JOB"

set +e
sg docker -c "clm-harbor run \
  --path $DATA/tasks/$SET \
  -a clm-minimal -m openai/qwen3.5-9b \
  --clm-config bcp \
  --agent-kwarg api_base=http://127.0.0.1:9009/v1 \
  --agent-kwarg max_steps=$MAX_STEPS \
  --agent-kwarg flops_model_key=9b \
  --agent-kwarg flops_tokenizer=$HOME/models/Qwen3.5-9B \
  --agent-kwarg skill_dirs=$REPO/bcp/skills/bcp_search_v1 \
  --n-concurrent $N_CONCURRENT \
  --jobs-dir $DATA/jobs --job-name $JOB \
  --yes --quiet $*"
STATUS=$?
set -e
echo "harbor exit status: $STATUS"

if [ "${KEEP_SERVERS:-0}" != 1 ] && [ "${JUDGE:-1}" = 1 ]; then
  "$REPO/bcp/servers.sh" stop
  "$REPO/bcp/servers.sh" judge-start
  python3 -I "$REPO/bcp/judge.py" --job-dir "$DATA/jobs/$JOB" || echo "judge failed" >&2
fi
exit $STATUS
