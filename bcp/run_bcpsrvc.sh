#!/usr/bin/env bash
# The solver-reader-verifier-compaction BCP-harness v0.1 (claude_docs/12 §5): the CLM agent's
# loop and compaction (bcp/bcp_srvc.py, a thin subclass of ClmAgent) with the skill
# bcp_solver_v1, whose commands search, read_document and verify are served by the tools
# server (reader and verifier are model calls made there).
#
#   bcp/run_bcpsrvc.sh <set> <job-name> [extra harbor args...]
#   bcp/run_bcpsrvc.sh smoke5 bcpsrvc-smoke
#   bcp/run_bcpsrvc.sh dev50  bcpsrvc-dev50
#
# Agent settings are the released configs/bcp.yaml of CLM with the baseline's two changes
# (max_steps 100, flops_model_key 9b) and command_timeout 1800 (a search waits for its reader
# calls). Servers, judge and output directory as in bcp/run_clm_base.sh.
# Environment: N_CONCURRENT (16), MAX_NUM_SEQS (32), MAX_STEPS (100), KEEP_SERVERS=1, JUDGE=0.
#
# VERSION=v02 runs BCP-harness v0.2 (claude_docs/12 §13): the same agent, tasks, verifier and
# gate; the reader sees the first 512 tokens of a document and opens more itself
# (bcp/roles_v02.py, skill reader_open_bcp_v2), and the solver's skill is bcp_solver_v2 (the
# same commands; its text says what the reader now does).
#   VERSION=v02 bcp/run_bcpsrvc.sh dev50 bcpsrvc2-dev50
set -euo pipefail
SET="${1:?set: smoke5 | dev50 | dev150 | dev150-50 | first<N>}"; JOB="${2:?job name}"; shift 2
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${BCP_DATA:-$HOME/data/browsecomp-plus}"
N_CONCURRENT="${N_CONCURRENT:-16}"
MAX_STEPS="${MAX_STEPS:-100}"
export PATH="$HOME/envs/clm/bin:$PATH"
export OPENAI_API_KEY=token-abc123   # the local vLLM server's fixed key, not a secret
export PYTHONPATH="$REPO/bcp${PYTHONPATH:+:$PYTHONPATH}"
VERSION="${VERSION:-v01}"
case "$VERSION" in
  v01) SOLVER_SKILL=bcp_solver_v1; export TOOLS_ARGS="${TOOLS_ARGS:-}" ;;
  v02) SOLVER_SKILL=bcp_solver_v2; export TOOLS_ARGS="--reader-mode open --reader-skill reader_open_bcp_v2 ${TOOLS_ARGS:-}" ;;
  *) echo "unknown VERSION $VERSION" >&2; exit 1 ;;
esac

[ -d "$DATA/tasks/$SET-v01" ] || python3 "$REPO/bcp/build_tasks.py" tasks --set "$SET" --variant v01
[ -e "$DATA/jobs/$JOB" ] && { echo "$DATA/jobs/$JOB exists" >&2; exit 1; }

# The released bcp.yaml, as agent kwargs, with our overrides; its env entries are exported.
KWARGS="$(python - "$MAX_STEPS" "$HOME/models/Qwen3.5-9B" "$REPO/bcp/skills/$SOLVER_SKILL" "$SOLVER_SKILL" <<'PY'
import pathlib, sys, yaml, clm_harness
cfg = yaml.safe_load((pathlib.Path(clm_harness.__file__).parent / "configs/bcp.yaml").read_text())
kw = dict(cfg["agent_kwargs"])
kw.update(max_steps=sys.argv[1], flops_model_key="9b", flops_tokenizer=sys.argv[2], skill_dirs=sys.argv[3],
          command_timeout=1800, api_base="http://127.0.0.1:9009/v1", solver_skill_name=sys.argv[4])
fmt = lambda v: ("true" if v else "false") if isinstance(v, bool) else str(v)
print(" ".join(f"--agent-kwarg {k}={fmt(v)}" for k, v in kw.items()))
for k, v in (cfg.get("env") or {}).items():
    print(f"ENV {k}={fmt(v)}", file=sys.stderr)
PY
)"
export CLM_EDIT_GATE=shrink          # the env entry of bcp.yaml

cleanup() { [ "${KEEP_SERVERS:-0}" = 1 ] || "$REPO/bcp/servers.sh" stop; }
trap cleanup EXIT
[ -f "$DATA/run/vllm.pid" ] || ROLES=1 MAX_NUM_SEQS="${MAX_NUM_SEQS:-32}" "$REPO/bcp/servers.sh" start "$JOB"

set +e
sg docker -c "harbor run \
  --path $DATA/tasks/$SET-v01 \
  -a bcp_srvc:BcpSRVC -m openai/qwen3.5-9b \
  $KWARGS \
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
