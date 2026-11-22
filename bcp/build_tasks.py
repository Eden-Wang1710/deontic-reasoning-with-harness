#!/usr/bin/env python3
"""Build Harbor tasks for BrowseComp-Plus questions (claude_docs/12 §3.3, §3.5).

  build_tasks.py order                 write bcp/query_order.json (once; committed, ids only)
  build_tasks.py tasks --set dev50     write <data>/tasks/dev50/bcp-q<id>/ for every id of the set

Sets are prefixes of one fixed order: smoke5, dev50, dev150, or any `first<N>`; `dev150-50`
is dev150 without dev50, `rest780` is everything after dev50. Task directories hold the decrypted question, so they are written
under <data> (outside the repository) and never committed. Standard library only.
"""
import argparse, json, random, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = HERE / "query_order.json"
SEED = 20261121
SETS = {"smoke5": (0, 5), "dev50": (0, 50), "dev150": (0, 150), "dev150-50": (50, 150),
        "rest780": (50, 830), "all": (0, 830)}

INSTRUCTION = """You are a deep research agent. You need to answer the given question by searching a fixed \
document collection with the {commands} commands. Please perform reasoning and \
use the commands step by step, in an interleaved manner. You may use the {commands} \
commands multiple times. The collection is your only source: there is no \
internet access.

Question: {question}

When you have your final answer, write your response to the file `/app/answer.md` and then \
submit. A missing or empty `/app/answer.md` counts as no answer. The response in the file \
should be in the following format:
Explanation: {{your explanation for your final answer. For this explanation section only, you should cite your evidence documents inline by enclosing their docids in square brackets [] at the end of sentences. For example, [20].}}
Exact Answer: {{your succinct, final answer}}
Confidence: {{your confidence score between 0% and 100% for your answer}}
"""

TASK_TOML = """schema_version = "1.3"
artifacts = []

[task]
name = "bcp/q{qid}"
description = "BrowseComp-Plus question {qid}"
authors = []
keywords = []

[metadata]
query_id = "{qid}"

[verifier]
timeout_sec = 120.0

[agent]
timeout_sec = {agent_timeout}.0

[environment]
docker_image = "bcp-sandbox:1"
network_mode = "{network_mode}"
{allowed}build_timeout_sec = 600.0
os = "linux"

[environment.env]
BCP_QID = "{qid}"
BCP_TOOLS_ADDR = "{tools_addr}"
"""

# The two arms differ in the names of the commands only (claude_docs/12 §4, §5).
COMMANDS = {"base": "`search` and `get_document`", "v01": "`search` and `read_document`"}

# The answer file is only stored. Grading happens after the job, with a judge model
# (bcp/judge.py); the reward written here is a placeholder and is never read.
TEST_SH = """#!/bin/bash
mkdir -p /logs/verifier
if [ -s /app/answer.md ]; then cp /app/answer.md /logs/verifier/answer.md; fi
echo 0 > /logs/verifier/reward.txt
"""


def set_ids(name: str) -> list[str]:
    order = json.loads(ORDER.read_text())["order"]
    if name in SETS:
        a, b = SETS[name]
    elif name.startswith("first") and name[5:].isdigit():
        a, b = 0, int(name[5:])
    else:
        sys.exit(f"unknown set {name!r}")
    return order[a:b]


def cmd_order(args) -> None:
    if ORDER.exists():
        sys.exit(f"{ORDER} exists; the order is fixed and is not drawn again")
    ids = sorted((json.loads(l)["query_id"] for l in open(Path(args.data) / "prepared/queries.jsonl")), key=int)
    assert len(ids) == 830 and len(set(ids)) == 830
    random.Random(SEED).shuffle(ids)
    ORDER.write_text(json.dumps({
        "note": "BrowseComp-Plus query ids, sorted numerically and shuffled once with random.Random(seed). "
                "smoke5, dev50 and dev150 are the first 5, 50 and 150.",
        "seed": SEED, "order": ids}, indent=1) + "\n")
    print("wrote", ORDER)


def cmd_tasks(args) -> None:
    queries = {}
    for line in open(Path(args.data) / "prepared/queries.jsonl", encoding="utf-8"):
        row = json.loads(line); queries[row["query_id"]] = row["query"]
    out = Path(args.data) / "tasks" / (args.set if args.variant == "base" else f"{args.set}-{args.variant}")
    if out.exists():
        shutil.rmtree(out)
    allowed = f'allowed_hosts = ["{args.tools_addr.split(":")[0]}"]\n' if args.network_mode == "allowlist" else ""
    for qid in set_ids(args.set):
        d = out / f"bcp-q{qid}"
        (d / "environment").mkdir(parents=True); (d / "tests").mkdir()
        (d / "instruction.md").write_text(INSTRUCTION.format(question=queries[qid].strip(), commands=COMMANDS[args.variant]), encoding="utf-8")
        (d / "task.toml").write_text(TASK_TOML.format(qid=qid, agent_timeout=args.agent_timeout,
                                                      network_mode=args.network_mode, allowed=allowed,
                                                      tools_addr=args.tools_addr))
        shutil.copy(HERE / "sandbox/Dockerfile", d / "environment/Dockerfile")
        (d / "tests/test.sh").write_text(TEST_SH); (d / "tests/test.sh").chmod(0o755)
    print(f"{args.set} ({args.variant}): {len(set_ids(args.set))} tasks in {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path.home() / "data/browsecomp-plus"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("order")
    t = sub.add_parser("tasks")
    t.add_argument("--set", required=True)
    t.add_argument("--tools-addr", default="172.17.0.1:9010")
    t.add_argument("--network-mode", choices=["allowlist", "public"], default="allowlist")
    t.add_argument("--agent-timeout", type=int, default=14400)
    t.add_argument("--variant", choices=["base", "v01"], default="base")
    a = ap.parse_args()
    cmd_order(a) if a.cmd == "order" else cmd_tasks(a)
