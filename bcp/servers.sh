#!/usr/bin/env bash
# Start or stop the two host servers of a BrowseComp-Plus agent run (claude_docs/12 §3):
#   the tools server (bcp/tools_server.py, port 9010) and vLLM with Qwen3.5-9B (port 9009).
#
#   bcp/servers.sh start <tag>     tag names the call log: <data>/jobs/<tag>.tools.jsonl
#                                  ROLES=1 also serves the reader and the verifier of v0.1
#   bcp/servers.sh stop
#   bcp/servers.sh status
#   bcp/servers.sh judge-start     vLLM with the judge model (Qwen3.5-35B-A3B) alone
#
# Rule of this machine: the servers are started for a run and stopped when it ends.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DATA="${BCP_DATA:-$HOME/data/browsecomp-plus}"
PY="$HOME/miniconda3/envs/myenv/bin/python"
RUN="$DATA/run"; mkdir -p "$RUN" "$DATA/jobs" "$HOME/vllm_logs"
KEY=token-abc123   # the local server's fixed key (tools/serve_qwen35_35b.sh), not a secret

wait_http() {  # url, seconds, [auth]
  local t=0
  until curl -sf -m 5 ${3:+-H "Authorization: Bearer $KEY"} "$1" >/dev/null 2>&1; do
    sleep 5; t=$((t + 5))
    if [ "$t" -ge "$2" ]; then echo "timeout waiting for $1" >&2; return 1; fi
  done
}
stop_one() {  # pid file
  if [ -f "$1" ]; then
    local pid; pid="$(cat "$1")"
    kill -TERM -- "-$pid" 2>/dev/null || true
    for _ in $(seq 30); do kill -0 -- "-$pid" 2>/dev/null || break; sleep 1; done
    kill -KILL -- "-$pid" 2>/dev/null || true
    rm -f "$1"
  fi
}
start_vllm() {  # model dir, served name, log, max seqs, gpu fraction
  MODEL_DIR="$1" SERVED_NAME="$2" LOG="$3" MAX_NUM_SEQS="$4" GPU_MEM_UTIL="$5" \
    setsid "$REPO/tools/serve_qwen35_35b.sh" >/dev/null 2>&1 &
  echo $! > "$RUN/vllm.pid"
  wait_http "http://127.0.0.1:9009/v1/models" 2400 auth
}

case "${1:?start <tag> | stop | status | judge-start}" in
  start)
    TAG="${2:?tag}"
    [ -f "$RUN/vllm.pid" ] || [ -f "$RUN/tools.pid" ] && { echo "servers already started; run stop first" >&2; exit 1; }
    setsid "$PY" -I "$REPO/bcp/tools_server.py" serve ${ROLES:+--roles} --log "$DATA/jobs/$TAG.tools.jsonl" \
      >"$DATA/jobs/$TAG.tools.log" 2>&1 &
    echo $! > "$RUN/tools.pid"
    wait_http "http://172.17.0.1:9010/health" 600
    start_vllm "$HOME/models/Qwen3.5-9B" qwen3.5-9b "$HOME/vllm_logs/qwen35-9b.log" "${MAX_NUM_SEQS:-16}" "${GPU_MEM_UTIL:-0.5}"
    echo "servers up: tools :9010, qwen3.5-9b :9009" ;;
  judge-start)
    [ -f "$RUN/vllm.pid" ] && { echo "a model server is already started; run stop first" >&2; exit 1; }
    start_vllm "$HOME/models/Qwen3.5-35B-A3B" qwen3.5-35b-a3b "$HOME/vllm_logs/qwen35-35b-a3b.log" 8 0.75
    echo "judge up: qwen3.5-35b-a3b :9009" ;;
  stop)
    stop_one "$RUN/vllm.pid"; stop_one "$RUN/tools.pid"; echo "servers stopped" ;;
  status)
    for f in vllm tools; do
      if [ -f "$RUN/$f.pid" ] && kill -0 -- "-$(cat "$RUN/$f.pid")" 2>/dev/null; then echo "$f: running"; else echo "$f: not running"; fi
    done
    nvidia-smi --query-compute-apps=pid,used_memory,name --format=csv,noheader 2>/dev/null || true ;;
  *) echo "unknown command $1" >&2; exit 1 ;;
esac
