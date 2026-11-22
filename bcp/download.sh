#!/usr/bin/env bash
# Downloads for the BrowseComp-Plus baseline (claude_docs/12 §3). No GPU use.
set -u
HF=~/miniconda3/envs/myenv/bin/hf
D=~/data/browsecomp-plus
$HF download Tevatron/browsecomp-plus-indexes --repo-type dataset --include "qwen3-embedding-8b/*" --local-dir $D/indexes && echo "DONE indexes"
$HF download Tevatron/browsecomp-plus-corpus  --repo-type dataset --local-dir $D/corpus  && echo "DONE corpus"
$HF download Tevatron/browsecomp-plus         --repo-type dataset --local-dir $D/queries_encrypted && echo "DONE queries"
$HF download Qwen/Qwen3-Embedding-8B --local-dir ~/models/Qwen3-Embedding-8B && echo "DONE embedding"
$HF download Qwen/Qwen3.5-9B         --local-dir ~/models/Qwen3.5-9B         && echo "DONE 9b"
du -sh $D/* ~/models/Qwen3-Embedding-8B ~/models/Qwen3.5-9B
